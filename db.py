"""
PostgreSQL 持久化模块（由 SQLite 迁移而来）

设计：
- requirements 表用 JSON 字段存整条需求（避免字段拆分迁移成本）
- attachments 文件存到 data/attachments/<req_id>/ 目录，元数据存到 requirement 的 attachments JSON 字段
- users / projects 用 PostgreSQL 表持久化
- 连接信息从 .env 读取（DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD）
"""
from __future__ import annotations
import os
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta

import psycopg2
from psycopg2 import pool as _pg_pool
from dotenv import load_dotenv

from auth import hash_password, verify_password


APP_DIR = Path(__file__).parent
load_dotenv(APP_DIR / ".env")

DATA_DIR = APP_DIR / "data"
ATTACH_DIR = DATA_DIR / "attachments"

# PostgreSQL 连接配置（从环境变量读取，.env 由 python-dotenv 加载）
# 本机部署时 DB_HOST / DB_PORT 无需填写，默认 localhost:5432；远程部署才需要配置
DB_HOST = os.environ.get("DB_HOST") or "localhost"
DB_PORT = os.environ.get("DB_PORT") or "5432"
DB_NAME = os.environ.get("DB_NAME") or "kanban_demo"
DB_USER = os.environ.get("DB_USER") or "postgres"
DB_PASSWORD = os.environ.get("DB_PASSWORD") or ""


# ============================================================
# 连接与执行辅助
# ============================================================

# 连接池：Streamlit 每次 UI 交互都会重跑整个脚本，若每条 SQL 都新建连接，
# 单次交互会产生十余次 TCP 建连（Windows 上 PostgreSQL 每次连接都要新起一个后端进程，
# 实测约 62ms/次），这是页面卡顿与服务器压力的主因。
# 各会话运行在同一进程的不同 ScriptRunner 线程中，因此用线程安全的模块级单例池共享连接。
_POOL: "_pg_pool.ThreadedConnectionPool | None" = None
_POOL_LOCK = threading.Lock()

_POOL_MIN_CONN = 1
_POOL_MAX_CONN = 20

_UNICODE_ERR_HINT = (
    "数据库连接失败：服务器返回的错误信息无法按 UTF-8 解析（通常是 PostgreSQL 的 "
    "lc_messages 被设成了中文/GBK 编码）。请先确认 .env 里的 DB_* 账号密码正确；"
    "若正确仍报此错，请把 postgresql.conf 里的 lc_messages 改为 'C' 并重启 PostgreSQL。"
)

_CONN_KWARGS = {
    "host": DB_HOST,
    "port": DB_PORT,
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
}


def _connect() -> "psycopg2.extensions.connection":
    """新建一个独立连接（不经过池）。池不可用时的兜底路径。"""
    try:
        conn = psycopg2.connect(**_CONN_KWARGS)
    except UnicodeDecodeError:
        # 中文版 PostgreSQL 的 lc_messages 默认是 GBK，错误信息会让 psycopg2 解码失败，
        # 从而把「密码错误」等真实原因掩盖成 UnicodeDecodeError。这里转成清晰提示。
        raise RuntimeError(_UNICODE_ERR_HINT) from None
    conn.autocommit = True
    return conn


def _get_pool() -> "_pg_pool.ThreadedConnectionPool":
    """懒加载连接池（双重检查，避免并发重复建池）"""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                try:
                    _POOL = _pg_pool.ThreadedConnectionPool(
                        _POOL_MIN_CONN, _POOL_MAX_CONN, **_CONN_KWARGS
                    )
                except UnicodeDecodeError:
                    raise RuntimeError(_UNICODE_ERR_HINT) from None
    return _POOL


@contextmanager
def _conn():
    """从池中借出连接，用完归还；出错的连接直接丢弃，不放回池中。

    连接在异常后可能停留在 aborted 事务状态，若放回池里会污染后续所有借用者，
    因此异常路径必须 close=True。
    """
    try:
        pool = _get_pool()
    except RuntimeError:
        raise
    except Exception:
        # 池建不起来（例如启动瞬间 PG 尚未就绪）时退化为直连，保证功能不中断
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()
        return

    try:
        conn = pool.getconn()
    except _pg_pool.PoolError:
        # 池被借空（极端并发突发）时退化为直连，宁可慢一点也不向用户报错
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()
        return

    # 归还的连接可能已被服务端断开（如 PG 重启），这里兜底重建
    if conn.closed:
        pool.putconn(conn, close=True)
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()
        return

    if not conn.autocommit:
        conn.autocommit = True
    try:
        yield conn
    except Exception:
        pool.putconn(conn, close=True)
        raise
    else:
        pool.putconn(conn)


def _query(sql: str, params: tuple = ()) -> list[tuple]:
    """执行查询并返回全部结果行"""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _query_one(sql: str, params: tuple = ()) -> tuple | None:
    rows = _query(sql, params)
    return rows[0] if rows else None


def _execute(sql: str, params: tuple = ()) -> None:
    """执行写操作（autocommit 已开启，无需显式 commit）"""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)


# ============================================================
# 初始化
# ============================================================

# 一次性清空的演示数据 ID（v2.0 进入真实使用，删除内置演示需求/项目）
_DEMO_REQ_IDS = [f"REQ-2026-{i:04d}" for i in range(1, 16)]
_DEMO_PROJ_IDS = ["P001", "P002", "P003", "P004"]

# init_db 的内容全是 CREATE TABLE IF NOT EXISTS / 空库判断，进程内跑第二次不产生任何效果。
# 但 Streamlit 每次 UI 交互都会调用它，实测单次约 650ms，因此进程内只允许执行一次。
_INITIALIZED = False
_INIT_LOCK = threading.Lock()


def init_db(
    sample_requirements: list[dict],
    sample_projects: list[dict] | None = None,
    seed_users: list[dict] | None = None,
) -> None:
    """初始化数据库：建表 + 一次性迁移 + 如空表则导入种子数据（进程内仅执行一次）"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _init_db_once(sample_requirements, sample_projects, seed_users)
        _INITIALIZED = True


def _init_db_once(
    sample_requirements: list[dict],
    sample_projects: list[dict] | None = None,
    seed_users: list[dict] | None = None,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)

    _execute("""
        CREATE TABLE IF NOT EXISTS requirements (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    _execute("CREATE INDEX IF NOT EXISTS idx_req_created ON requirements(created_at)")

    # 项目表
    _execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT NOT NULL,
            highlight TEXT,
            created_at TEXT
        )
    """)
    # 迁移：为旧库补「核心亮点/定制需求」列
    _execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS highlight TEXT")

    # 用户表（v2.0 新增，支持登录）
    _execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            department TEXT,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT,
            phone TEXT,
            email TEXT,
            dingtalk_userid TEXT,
            notify_by_group BOOLEAN DEFAULT TRUE
        )
    """)
    # 迁移：为旧库用户表补「手机号」「邮箱」「钉钉 userid」「是否在提醒群」列（提醒 @ 到人 / 分区提醒用）
    _execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
    _execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
    _execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dingtalk_userid TEXT")
    _execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_by_group BOOLEAN DEFAULT TRUE")

    # 系统使用体验问卷（评价体系二期）
    # 设计：弹窗「打开即写一条 dismissed」视为已触达，用户提交时原地升级为 submitted。
    # 这样即使 Streamlit 无法捕获弹窗被 X 关闭的事件，冷却期与触达率口径依然准确
    # （见 app.py 的 _maybe_show_survey / SURVEY_COOLDOWN_DAYS）。
    _execute("""
        CREATE TABLE IF NOT EXISTS sus_survey (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            role TEXT,
            department TEXT,
            response_type TEXT NOT NULL,
            score REAL,
            answers TEXT,
            features TEXT,
            comment TEXT,
            created_at TEXT,
            submitted_at TEXT
        )
    """)
    _execute("CREATE INDEX IF NOT EXISTS idx_survey_user ON sus_survey(user_id, created_at)")
    # 问卷题干版本标记（评价体系三期，03 蓝图 §4.3）：题干冻结纪律——改题干=换指标，
    # 未来改题/加题必须递增 question_set 并在统计时按版本分组，禁止跨版本混算均分。
    # 旧行 NULL 一律视作 v1（应用层归一化），既有列零改动（唯一允许的 sus_survey 结构变更）。
    _execute("ALTER TABLE sus_survey ADD COLUMN IF NOT EXISTS question_set TEXT")

    # 评估改进闭环（评价体系三期，03 蓝图 §5.2）：问卷/评价建议 → 改进项 → 推进 → 回告 的落库载体。
    # 纯增量幂等：老库执行一次即具备；老代码不感知本表，回滚零影响。
    _execute("""
        CREATE TABLE IF NOT EXISTS eval_improvement (
            id              TEXT PRIMARY KEY,          -- 'IMP-{yyyymmddHHMMSS}-{proposer_id}'，风格对齐 SV-{...}
            source_type     TEXT NOT NULL,             -- 'survey' | 'rating_comment' | 'manual'
            source_id       TEXT,                      -- sus_survey.id / '{req_id}::{user_id}' / NULL
            proposer_id     TEXT NOT NULL,             -- 提出人 user_id（回告目标）
            proposer_name   TEXT,                      -- 姓名快照（对齐 sus_survey.username 的冗余设计）
            summary         TEXT NOT NULL,             -- 一句话标题（回告文案用；缺省取 detail 前 30 字）
            detail          TEXT NOT NULL,             -- 原文（≤500 字，随问卷 max_chars 口径）
            category        TEXT,                      -- 归类：功能/体验/AI/数据/其他（管理层填写）
            status          TEXT NOT NULL DEFAULT '待处理',
            owner_id        TEXT,                      -- 负责人（管理层指派，可空）
            resolution_note TEXT,                      -- 处理结论（上线说明 / 不采纳原因）
            created_at      TEXT NOT NULL,             -- 时间戳格式与 sus_survey.created_at 一致
            updated_at      TEXT,
            closed_at       TEXT,                      -- 进入终态时间（处理时长口径依赖）
            notified_at     TEXT,                      -- 外发回告成功时间戳（幂等防重发；站内提醒不依赖它）
            proposer_ack_at TEXT                       -- 提出人「知道了」时间戳（站内提醒条消除用）
        )
    """)
    _execute("CREATE INDEX IF NOT EXISTS idx_imp_status ON eval_improvement(status, created_at)")
    _execute("CREATE INDEX IF NOT EXISTS idx_imp_proposer ON eval_improvement(proposer_id, status)")
    # 部分唯一索引：同一问卷记录 / 同一条评价最多生成 1 个改进项（重试安全，冲突静默）；
    # manual（管理层自录）不在防重范围内，同一秒多条合法。
    _execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_imp_source
        ON eval_improvement(source_type, source_id)
        WHERE source_type <> 'manual'
    """)

    # 用户活跃埋点（评价体系 D4，t20 范围扩展）：登录与页面切换各写一条，统计近 1/7/30 天活跃。
    # 这是 mvp 第一张日志型增长表：id 用 BIGSERIAL 自增（纯追加、无业务语义，
    # 不对齐 SV-/IMP- 的业务编号风格）；统计一律限定 ≤30 天窗口并走 created_at 索引
    # （增长控制与保留策略见 05 §10：按窗口只查不删，不做自动清理）。
    _execute("""
        CREATE TABLE IF NOT EXISTS user_activity_log (
            id          BIGSERIAL PRIMARY KEY,
            user_id     TEXT NOT NULL,
            user_name   TEXT,
            role        TEXT,
            page_path   TEXT,                      -- 导航项中文名（mvp 无 URL 路由，与页面显示名一致）
            action      TEXT NOT NULL DEFAULT 'page_view',   -- 'login' | 'page_view'
            created_at  TEXT NOT NULL              -- 时间戳格式与 sus_survey.created_at 一致（字典序可比）
        )
    """)
    _execute("CREATE INDEX IF NOT EXISTS idx_act_created ON user_activity_log(created_at)")
    _execute("CREATE INDEX IF NOT EXISTS idx_act_user ON user_activity_log(user_id, created_at)")


    # 大项目名归一（幂等）：历史数据可能写作「其它」，统一为「其他」，避免固定分组分裂成两张卡
    _execute("UPDATE projects SET code = '其他' WHERE TRIM(code) = '其它'")

    # 元数据表（记录一次性迁移标记）
    _execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # 一次性迁移：清空内置演示需求与项目
    _migrate_clear_demo()

    # 空库时导入种子用户（仅首次）
    user_count = _query_one("SELECT COUNT(*) FROM users")[0]
    if user_count == 0 and seed_users:
        for u in seed_users:
            pwd = u.get("password") or "123456"
            h, salt = hash_password(pwd)
            _execute(
                "INSERT INTO users (id, name, role, department, password_hash, salt, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (u["id"], u["name"], u["role"], u.get("department", ""), h, salt,
                 datetime.now().strftime("%Y-%m-%d")),
            )

    # 空库时导入 sample 需求
    count = _query_one("SELECT COUNT(*) FROM requirements")[0]
    if count == 0 and sample_requirements:
        for req in sample_requirements:
            req_json = json.dumps(req, ensure_ascii=False)
            created = req.get("created_at") or datetime.now().strftime("%Y-%m-%d")
            _execute(
                "INSERT INTO requirements (id, data, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                (req["id"], req_json, created, created),
            )

    # 空库时导入示例项目
    proj_count = _query_one("SELECT COUNT(*) FROM projects")[0]
    if proj_count == 0 and sample_projects:
        for p in sample_projects:
            _execute(
                "INSERT INTO projects (id, name, code, created_at) VALUES (%s, %s, %s, %s)",
                (p["id"], p["name"], p["code"], datetime.now().strftime("%Y-%m-%d")),
            )


def _migrate_clear_demo() -> None:
    """一次性迁移：删除内置演示需求/项目，之后不再触发。"""
    done = _query_one("SELECT value FROM meta WHERE key = %s", ("cleared_demo_data",))
    if done:
        return
    for rid in _DEMO_REQ_IDS:
        _execute("DELETE FROM requirements WHERE id = %s", (rid,))
    for pid in _DEMO_PROJ_IDS:
        _execute("DELETE FROM projects WHERE id = %s", (pid,))
    _execute(
        "INSERT INTO meta (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        ("cleared_demo_data", "1"),
    )


# ============================================================
# 数据版本指纹
# ============================================================

def data_version() -> str:
    """返回一个能代表「需求/项目/用户」当前状态的轻量指纹（单次往返）。

    各用户会话把数据缓存在自己的 session_state 里，原本无法感知别人的改动
    （A 提的需求 B 看不到）。每次 rerun 比对这个指纹即可判断要不要重新加载。
    projects / users 表没有 updated_at，且改名不改变行数，所以对内容取 md5；
    这两张表都只有几十行，开销可以忽略。
    """
    try:
        row = _query_one(
            """
            SELECT
                (SELECT COUNT(*) FROM requirements),
                (SELECT COALESCE(MAX(updated_at), '') FROM requirements),
                (SELECT COALESCE(md5(string_agg(
                    id || name || code || COALESCE(highlight, ''), '|' ORDER BY id)), '')
                 FROM projects),
                (SELECT COALESCE(md5(string_agg(
                    id || name || role || COALESCE(department, '') || COALESCE(phone, '')
                    || COALESCE(email, '') || COALESCE(dingtalk_userid, ''), '|' ORDER BY id)), '')
                 FROM users)
            """
        )
    except Exception:
        # 指纹只用于「要不要刷新缓存」，任何异常都不应影响正常使用
        return ""
    return "|".join(str(c) for c in row) if row else ""


# ============================================================
# CRUD
# ============================================================

def load_all_requirements() -> list[dict]:
    rows = _query(
        "SELECT data FROM requirements ORDER BY created_at, id"
    )
    return [json.loads(r[0]) for r in rows]


def load_requirement(req_id: str) -> dict | None:
    row = _query_one(
        "SELECT data FROM requirements WHERE id = %s", (req_id,)
    )
    return json.loads(row[0]) if row else None


def save_requirement(req: dict) -> None:
    """Upsert：存在则更新，不存在则插入"""
    req_json = json.dumps(req, ensure_ascii=False)
    created = req.get("created_at") or datetime.now().strftime("%Y-%m-%d")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    _execute(
        """
        INSERT INTO requirements (id, data, created_at, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
        """,
        (req["id"], req_json, created, updated),
    )


def delete_requirement(req_id: str) -> None:
    _execute("DELETE FROM requirements WHERE id = %s", (req_id,))


def next_seq(month_prefix: str) -> int:
    """获取指定月份（yyyymm）内的下一个 REQ 序号，每个月从 1 开始"""
    rows = _query(
        "SELECT id FROM requirements WHERE id LIKE %s",
        (f"REQ-{month_prefix}-%",),
    )
    if not rows:
        return 1
    seqs = []
    for (rid,) in rows:
        try:
            seqs.append(int(rid.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return (max(seqs) + 1) if seqs else 1


# ============================================================
# 附件
# ============================================================

def save_attachment(req_id: str, filename: str, content: bytes, uploaded_by: str) -> dict:
    """保存附件文件到磁盘，返回元数据（不写入 requirements 表，由调用方负责）"""
    req_dir = ATTACH_DIR / req_id
    req_dir.mkdir(parents=True, exist_ok=True)

    # 防路径穿越与命名冲突
    safe_name = Path(filename).name.replace("/", "_").replace("\\", "_")
    target = req_dir / safe_name
    if target.exists():
        i = 1
        while (req_dir / f"{i}_{safe_name}").exists():
            i += 1
        safe_name = f"{i}_{safe_name}"
        target = req_dir / safe_name

    target.write_bytes(content)
    return {
        "filename": safe_name,
        "original_name": filename,
        "size": len(content),
        "path": str(target.relative_to(APP_DIR)),
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def read_attachment(req_id: str, filename: str) -> bytes | None:
    target = ATTACH_DIR / req_id / filename
    if target.exists() and target.is_file():
        return target.read_bytes()
    return None


def remove_attachment(req_id: str, filename: str) -> bool:
    target = ATTACH_DIR / req_id / filename
    if target.exists() and target.is_file():
        target.unlink()
        return True
    return False


# ============================================================
# 项目管理（v0.3.0 新增）
# ============================================================

def load_all_projects() -> list[dict]:
    rows = _query(
        "SELECT id, name, code, highlight FROM projects ORDER BY id"
    )
    return [{"id": r[0], "name": r[1], "code": r[2], "highlight": r[3] or ""} for r in rows]


def save_project(project: dict) -> None:
    _execute(
        """
        INSERT INTO projects (id, name, code, highlight, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, code = EXCLUDED.code, highlight = EXCLUDED.highlight
        """,
        (project["id"], project["name"], project.get("code", ""), project.get("highlight", ""),
         datetime.now().strftime("%Y-%m-%d")),
    )


def delete_project(project_id: str) -> None:
    _execute("DELETE FROM projects WHERE id = %s", (project_id,))


def next_project_seq() -> int:
    rows = _query(
        "SELECT id FROM projects WHERE id LIKE %s",
        ("P%",),
    )
    if not rows:
        return 1
    seqs = []
    for (pid,) in rows:
        try:
            seqs.append(int(pid[1:]))
        except ValueError:
            continue
    return (max(seqs) + 1) if seqs else 1


def db_stats() -> dict:
    """返回数据库基本统计，用于在 UI 显示"""
    try:
        req_count = _query_one("SELECT COUNT(*) FROM requirements")[0]
        proj_count = _query_one("SELECT COUNT(*) FROM projects")[0]
        size_bytes = _query_one("SELECT pg_database_size(current_database())")[0]
    except psycopg2.OperationalError:
        return {
            "exists": False,
            "size_kb": 0,
            "req_count": 0,
            "proj_count": 0,
            "attach_count": 0,
            "db_path": f"{DB_HOST}:{DB_PORT}/{DB_NAME}",
            "attach_dir": str(ATTACH_DIR.relative_to(APP_DIR)),
        }

    attach_count = 0
    if ATTACH_DIR.exists():
        for _ in ATTACH_DIR.rglob("*"):
            if _.is_file():
                attach_count += 1
    return {
        "exists": True,
        "size_kb": round(size_bytes / 1024, 1),
        "req_count": req_count,
        "proj_count": proj_count,
        "attach_count": attach_count,
        "db_path": f"{DB_HOST}:{DB_PORT}/{DB_NAME}",
        "attach_dir": str(ATTACH_DIR.relative_to(APP_DIR)),
    }


# ============================================================
# 用户管理（v2.0 新增，支持登录）
# ============================================================

def load_all_users() -> list[dict]:
    rows = _query(
        "SELECT id, name, role, department, created_at, phone, email, dingtalk_userid, "
        "COALESCE(notify_by_group, TRUE) FROM users ORDER BY id"
    )
    return [{"id": r[0], "name": r[1], "role": r[2], "department": r[3], "created_at": r[4],
             "phone": r[5], "email": r[6], "dingtalk_userid": r[7],
             "notify_by_group": bool(r[8])} for r in rows]


def authenticate(name: str, password: str) -> dict | None:
    """按姓名 + 密码校验，成功返回用户（不含密码字段），失败返回 None。"""
    row = _query_one(
        "SELECT id, name, role, department, password_hash, salt FROM users WHERE name = %s",
        (name,),
    )
    if not row:
        return None
    if not verify_password(password, row[5], row[4]):
        return None
    return {"id": row[0], "name": row[1], "role": row[2], "department": row[3]}


def save_user(user: dict, password: str | None = None) -> None:
    """Upsert 用户；password 非空时（重新）设置密码。"""
    existing = _query_one("SELECT id FROM users WHERE id = %s", (user["id"],))
    if existing:
        # phone/email/dingtalk_userid/notify_by_group 未提供时保持原值（如「修改密码」入口只传基本字段）
        _execute(
            "UPDATE users SET name = %s, role = %s, department = %s, "
            "phone = COALESCE(%s, phone), email = COALESCE(%s, email), "
            "dingtalk_userid = COALESCE(%s, dingtalk_userid), "
            "notify_by_group = COALESCE(%s, notify_by_group) WHERE id = %s",
            (user["name"], user["role"], user.get("department", ""), user.get("phone"),
             user.get("email"), user.get("dingtalk_userid"),
             user.get("notify_by_group"), user["id"]),
        )
        if password:
            h, salt = hash_password(password)
            _execute("UPDATE users SET password_hash = %s, salt = %s WHERE id = %s", (h, salt, user["id"]))
    else:
        h, salt = hash_password(password or "123456")
        _execute(
            "INSERT INTO users (id, name, role, department, phone, email, dingtalk_userid, "
            "notify_by_group, password_hash, salt, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user["id"], user["name"], user["role"], user.get("department", ""), user.get("phone"),
             user.get("email"), user.get("dingtalk_userid"), user.get("notify_by_group", True), h, salt,
             datetime.now().strftime("%Y-%m-%d")),
        )


def delete_user(user_id: str) -> None:
    _execute("DELETE FROM users WHERE id = %s", (user_id,))


def next_user_seq() -> int:
    rows = _query(
        "SELECT id FROM users WHERE id LIKE %s",
        ("U%",),
    )
    if not rows:
        return 1
    seqs = []
    for (uid,) in rows:
        try:
            seqs.append(int(uid[1:]))
        except ValueError:
            continue
    return (max(seqs) + 1) if seqs else 1


# ============================================================
# 系统使用体验问卷（评价体系二期）
# ============================================================

def new_survey_dismissed(user: dict) -> str:
    """问卷弹窗被触达时先落一条 dismissed 记录，返回记录 id（提交时凭 id 原地升级）。

    先写 dismissed 的原因见建表处注释：Streamlit 捕获不到弹窗的 X 关闭动作，
    把「已触达」前置到打开时刻，冷却期才不会因为用户点 X 而失效。
    """
    now = datetime.now()
    rec_id = f"SV-{user['id']}-{now.strftime('%Y%m%d%H%M%S')}"
    _execute(
        "INSERT INTO sus_survey (id, user_id, username, role, department, response_type, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (rec_id, user["id"], user.get("name", ""), user.get("role", ""),
         user.get("department", ""), "dismissed", now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return rec_id


def submit_survey(rec_id: str, score: float, answers: dict, features: list, comment: str,
                  question_set: str = "v1") -> None:
    """把这条 dismissed 记录升级为 submitted，并写入答案。

    question_set 为题干版本标记（评价体系三期新增，缺省 v1）：旧调用不传即按 v1 落库，
    提交口径与既有列（score=4 题 1-5 均分）不变。
    """
    _execute(
        "UPDATE sus_survey SET response_type = 'submitted', score = %s, answers = %s, "
        "features = %s, comment = %s, submitted_at = %s, question_set = %s WHERE id = %s",
        (score, json.dumps(answers, ensure_ascii=False),
         json.dumps(features or [], ensure_ascii=False),
         (comment or "").strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), question_set, rec_id),
    )


def last_survey_at(user_id: str) -> str | None:
    """该用户最近一次被触达问卷的时间（提交或关闭都算），用于判断冷却期。"""
    row = _query_one(
        "SELECT created_at FROM sus_survey WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )
    return row[0] if row else None


def load_survey_responses() -> list[dict]:
    """全部问卷记录（含 dismissed），供统计页按 response_type 分流。

    question_set 为三期新增列：旧行为 NULL，应用层统计时一律视作 v1（03 蓝图 §4.3）。
    """
    rows = _query(
        "SELECT id, user_id, username, role, department, response_type, score, answers, "
        "features, comment, created_at, submitted_at, question_set FROM sus_survey ORDER BY created_at DESC"
    )
    out = []
    for r in rows:
        def _loads(v):
            try:
                return json.loads(v) if v else None
            except (ValueError, TypeError):
                return None
        out.append({
            "id": r[0], "user_id": r[1], "username": r[2], "role": r[3],
            "department": r[4], "response_type": r[5], "score": r[6],
            "answers": _loads(r[7]), "features": _loads(r[8]) or [],
            "comment": r[9] or "", "created_at": r[10], "submitted_at": r[11],
            "question_set": r[12],
        })
    return out


# ============================================================
# 评估改进闭环（评价体系三期）
# ============================================================

# 状态机取值与合法流转白名单（03 蓝图 §5.3）：
#   待处理 ──认领/指派──→ 处理中；待处理/处理中 ──关闭──→ 不采纳|已搁置；
#   处理中 ──完成──→ 已上线；已搁置 ──重新激活──→ 待处理（唯一终态回流，供误操作纠正）。
# DAO 层强制校验（UI 按钮组只渲染合法目标状态，双层防护，03 蓝图 §5.4）。
IMPROVEMENT_STATUSES = ("待处理", "处理中", "已上线", "不采纳", "已搁置")
IMPROVEMENT_TERMINAL = ("已上线", "不采纳", "已搁置")
IMPROVEMENT_TRANSITIONS = {
    "待处理": ("处理中", "不采纳", "已搁置"),
    "处理中": ("已上线", "不采纳", "已搁置"),
    "已上线": (),
    "不采纳": (),
    "已搁置": ("待处理",),
}

# 管理层角色常量（与用户表 role 取值一致；闭环的改/关/告操作仅管理层可执行）
_IMPROVEMENT_ADMIN_ROLE = "管理层"


def create_improvement(source_type: str, source_id: str | None, proposer_id: str,
                       proposer_name: str = "", summary: str = "", detail: str = "",
                       category: str = "") -> str | None:
    """新建改进项，返回新记录 id。

    - 非 manual 来源命中 uq_imp_source 部分唯一索引时静默返回已存在项的 id（自动建项可安全重试）；
    - 同一秒内多条记录 id 冲突（PK）时自动追加序号重试；
    - detail 统一 strip 并截断到 500 字（随问卷 max_chars 口径）；summary 缺省取 detail 前 30 字。
    """
    if source_type not in ("survey", "rating_comment", "manual"):
        raise ValueError(f"未知来源类型：{source_type}")
    detail = (detail or "").strip()[:500]
    summary = (summary or "").strip() or (detail[:30] or "（无内容）")
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    base = f"IMP-{now.strftime('%Y%m%d%H%M%S')}-{proposer_id}"
    imp_id, seq = base, 1
    while True:
        try:
            _execute(
                "INSERT INTO eval_improvement (id, source_type, source_id, proposer_id, proposer_name, "
                "summary, detail, category, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (imp_id, source_type, source_id, proposer_id, proposer_name or "",
                 summary, detail, (category or "").strip(), "待处理", now_str, now_str),
            )
            return imp_id
        except psycopg2.errors.UniqueViolation as e:
            cname = e.diag.constraint_name if e.diag else None
            if cname == "eval_improvement_pkey":
                # 同秒多条（如管理层连续自录）：追加序号保证 id 唯一
                seq += 1
                imp_id = f"{base}-{seq}"
                continue
            if cname == "uq_imp_source":
                # 同一问卷记录/同一条评价已有改进项：静默返回已存在 id（防重）
                row = _query_one(
                    "SELECT id FROM eval_improvement WHERE source_type = %s AND source_id = %s",
                    (source_type, source_id),
                )
                return row[0] if row else None
            raise


def load_improvements(status: str | None = None, proposer_id: str | None = None,
                      with_proposer: bool = True) -> list[dict]:
    """改进项列表（按创建时间倒序）。

    with_proposer=False 时取数层即不查 proposer_name（主管的匿名化视图在取数层实现，
    不在前端遮挡，防止导出泄漏，03 蓝图 §5.6-7）；role/department 来自 users 表联查。
    """
    sql = (
        "SELECT i.id, i.source_type, i.source_id, i.proposer_id, "
        + ("i.proposer_name, " if with_proposer else "NULL, ")
        + "u.role, u.department, i.summary, i.detail, i.category, i.status, i.owner_id, "
        "i.resolution_note, i.created_at, i.updated_at, i.closed_at, i.notified_at, i.proposer_ack_at "
        "FROM eval_improvement i LEFT JOIN users u ON u.id = i.proposer_id"
    )
    conds, params = [], []
    if status:
        conds.append("i.status = %s")
        params.append(status)
    if proposer_id:
        conds.append("i.proposer_id = %s")
        params.append(proposer_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY i.created_at DESC, i.id"
    try:
        rows = _query(sql, tuple(params))
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as e:
        # F-04 优雅降级：新表尚未迁移（如运行中被替换代码、迁移未执行）时返回空列表，
        # 不让评估页/全员公示区块抛异常拖垮整站；管理员看日志即可定位
        print(f"[eval] 改进闭环数据表尚未迁移，改进项列表暂不可用：{e}")
        return []
    out = []
    for r in rows:
        out.append({
            "id": r[0], "source_type": r[1], "source_id": r[2], "proposer_id": r[3],
            "proposer_name": r[4], "proposer_role": r[5] or "", "proposer_dept": r[6] or "",
            "summary": r[7], "detail": r[8], "category": r[9] or "", "status": r[10],
            "owner_id": r[11] or "", "resolution_note": r[12] or "", "created_at": r[13] or "",
            "updated_at": r[14] or "", "closed_at": r[15] or "", "notified_at": r[16] or "",
            "proposer_ack_at": r[17] or "",
        })
    return out


def get_improvement(imp_id: str, with_proposer: bool = True) -> dict | None:
    """取单条改进项（回告/确认等单条操作用）。"""
    rows = load_improvements(with_proposer=with_proposer)
    return next((i for i in rows if i["id"] == imp_id), None)


def update_improvement_status(imp_id: str, new_status: str, actor_role: str,
                              resolution_note: str | None = None) -> tuple[bool, str]:
    """推进改进项状态。DAO 层双重校验：操作者必须是管理层 + 流转必须在白名单内。

    返回 (是否成功, 提示信息)；非法流转/越权一律拒绝并给出原因（UI 直接展示提示）。
    进入终态写 closed_at；已搁置→待处理回流时清掉 closed_at（处理时长口径不被旧值污染）。
    """
    if actor_role != _IMPROVEMENT_ADMIN_ROLE:
        return False, "仅管理层可以推进改进项状态。"
    if new_status not in IMPROVEMENT_STATUSES:
        return False, f"未知状态「{new_status}」，合法取值：{'、'.join(IMPROVEMENT_STATUSES)}。"
    row = _query_one("SELECT status FROM eval_improvement WHERE id = %s", (imp_id,))
    if not row:
        return False, f"改进项 {imp_id} 不存在。"
    cur_status = row[0]
    if new_status == cur_status:
        return False, f"改进项当前已是「{cur_status}」，无需重复操作。"
    if new_status not in IMPROVEMENT_TRANSITIONS.get(cur_status, ()):
        return False, f"非法流转：改进项当前为「{cur_status}」，不能直接变更为「{new_status}」。"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note_sql, note_params = "", []
    if resolution_note is not None:
        note_sql = ", resolution_note = %s"
        note_params.append((resolution_note or "").strip()[:500])
    if new_status in IMPROVEMENT_TERMINAL:
        _execute(
            f"UPDATE eval_improvement SET status = %s{note_sql}, updated_at = %s, closed_at = %s WHERE id = %s",
            (new_status, *note_params, now, now, imp_id),
        )
    else:
        _execute(
            f"UPDATE eval_improvement SET status = %s{note_sql}, updated_at = %s, closed_at = NULL WHERE id = %s",
            (new_status, *note_params, now, imp_id),
        )
    return True, f"已推进为「{new_status}」。"


def assign_improvement_owner(imp_id: str, owner_id: str, actor_role: str) -> tuple[bool, str]:
    """指派/认领负责人（仅管理层）。owner_id 传空串表示取消指派。"""
    if actor_role != _IMPROVEMENT_ADMIN_ROLE:
        return False, "仅管理层可以指派改进项负责人。"
    row = _query_one("SELECT status FROM eval_improvement WHERE id = %s", (imp_id,))
    if not row:
        return False, f"改进项 {imp_id} 不存在。"
    if row[0] in IMPROVEMENT_TERMINAL:
        return False, f"改进项已处于终态「{row[0]}」，不能再指派负责人。"
    _execute(
        "UPDATE eval_improvement SET owner_id = %s, updated_at = %s WHERE id = %s",
        ((owner_id or "").strip() or None, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), imp_id),
    )
    return True, "已更新负责人。"


def update_improvement_meta(imp_id: str, actor_role: str,
                            summary: str | None = None, category: str | None = None) -> tuple[bool, str]:
    """修改管理层归纳的摘要/归类（仅管理层，回告文案与操作台展示用）。"""
    if actor_role != _IMPROVEMENT_ADMIN_ROLE:
        return False, "仅管理层可以修改改进项摘要/归类。"
    row = _query_one("SELECT summary, category FROM eval_improvement WHERE id = %s", (imp_id,))
    if not row:
        return False, f"改进项 {imp_id} 不存在。"
    sets, params = [], []
    if summary is not None:
        s = (summary or "").strip()[:100]
        if not s:
            return False, "摘要不能为空。"
        sets.append("summary = %s")
        params.append(s)
    if category is not None:
        sets.append("category = %s")
        params.append((category or "").strip())
    if not sets:
        return False, "没有需要修改的字段。"
    sets.append("updated_at = %s")
    params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    params.append(imp_id)
    _execute(f"UPDATE eval_improvement SET {', '.join(sets)} WHERE id = %s", tuple(params))
    return True, "已保存。"


def mark_improvement_notified(imp_id: str, actor_role: str) -> tuple[bool, str]:
    """标记「外发回告已送达」（仅管理层，幂等：已标记则跳过不重发）。

    语义（队长 t11 硬要求）：notified_at 只在外发通道确认受理后由调用方写入；
    站内提醒条的可见性不依赖本字段（见 pending_improvement_notice）。
    """
    if actor_role != _IMPROVEMENT_ADMIN_ROLE:
        return False, "仅管理层可以执行回告。"
    row = _query_one("SELECT notified_at FROM eval_improvement WHERE id = %s", (imp_id,))
    if not row:
        return False, f"改进项 {imp_id} 不存在。"
    if row[0]:
        return True, "已回告过（幂等跳过，不重复外发）。"
    _execute(
        "UPDATE eval_improvement SET notified_at = %s, updated_at = %s WHERE id = %s",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), imp_id),
    )
    return True, "已记录回告。"


def ack_improvement(imp_id: str, user_id: str) -> tuple[bool, str]:
    """提出人「知道了」确认（仅提出人本人可操作，幂等）。

    proposer_ack_at 是站内提醒条的唯一消除开关（03 蓝图 §5.5）。
    """
    row = _query_one("SELECT proposer_id, proposer_ack_at FROM eval_improvement WHERE id = %s", (imp_id,))
    if not row:
        return False, f"改进项 {imp_id} 不存在。"
    if row[0] != user_id:
        return False, "只有提出人本人可以确认。"
    if row[1]:
        return True, "已确认过（幂等跳过）。"
    _execute(
        "UPDATE eval_improvement SET proposer_ack_at = %s, updated_at = %s WHERE id = %s",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), imp_id),
    )
    return True, "已确认。"


def pending_improvement_notice(user_id: str) -> list[dict]:
    """提出人待确认「进展回告」的改进项（站内提醒条取数，03 蓝图 §5.5 + 队长 t11 硬要求）。

    口径：站内可见性只由「需回告终态 + 未确认(ack)」决定，不依赖外发是否成功——
    即使钉钉/邮件未配置或外发失败，提出人登录后也一定能在主区看到进展提醒；
    「已搁置」默认不打扰，仅在管理层批量补告（notified_at 已写）后纳入提醒。
    """
    try:
        rows = _query(
            "SELECT id, summary, status, resolution_note, closed_at, notified_at FROM eval_improvement "
            "WHERE proposer_id = %s AND proposer_ack_at IS NULL "
            "AND (status IN ('已上线', '不采纳') OR notified_at IS NOT NULL) "
            "ORDER BY COALESCE(closed_at, notified_at, created_at) DESC",
            (user_id,),
        )
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as e:
        # F-04 优雅降级：本函数挂在 main() 提醒区、每个用户每个页面都会调用，
        # 新表未迁移时必须返回空而非抛异常，否则故障面是全站而非单页
        print(f"[eval] 改进闭环数据表尚未迁移，回告提醒暂不可用：{e}")
        return []
    return [{
        "id": r[0], "summary": r[1], "status": r[2], "resolution_note": r[3] or "",
        "closed_at": r[4] or "", "notified_at": r[5] or "",
    } for r in rows]


# ============================================================
# 用户活跃埋点与统计（评价体系 D4，t20 范围扩展）
# ============================================================

def log_user_activity(user_id: str, user_name: str = "", role: str = "",
                      page_path: str = "", action: str = "page_view") -> None:
    """写一条用户活跃埋点：登录记 action='login'，页面切换记 action='page_view'。

    本函数只管写入；调用方（app.py 的登录成功分支与 main() 导航切换点）已 try/except 包裹——
    埋点失败只打日志、绝不阻塞登录与页面渲染（吸收客诉「埋点失败不抛异常」纪律）。
    """
    _execute(
        "INSERT INTO user_activity_log (user_id, user_name, role, page_path, action, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, (user_name or "").strip(), (role or "").strip(),
         (page_path or "").strip(), action or "page_view",
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def activity_stats() -> dict:
    """用户活跃统计（系统评估页「👥 用户活跃」区块取数）。

    口径（时间窗口基于 created_at 文本时间戳，'%Y-%m-%d %H:%M:%S' 字典序可比）：
    - dau / wau / mau：近 1 / 7 / 30 天内有任意埋点记录（login 或 page_view）的去重 user_id 数；
    - daily：近 30 个日历日（含今天）逐日聚合——users 为当日去重人数、events 为当日埋点条数
      （无记录的日期不返回行，展示层补 0）；
    - total_users / total_logins / total_views：累计口径——注册用户数（users 表全量）、
      埋点上线以来的登录与页面访问总次数（供向上汇报的「使用量」数字）；
    - page_heat：近 30 天 action='page_view' 且 page_path 非空的记录按页面计数，倒序
      （login 事件与空页面不计入热度）；
    - user_activity：近 30 天按 user_id 聚合：visits（全部动作计数）/ active_days（去重日期数，
      取 created_at 前 10 位 'YYYY-MM-DD'）/ last_active（最近 created_at），附姓名与角色快照；
    - has_data：表内是否存在任何记录（评估页空态判定）。

    增长控制：统计一律限定 ≤30 天窗口并走 created_at 索引（日志型增长表只查不删，
    保留策略见 05 §10）；未迁移态（表不存在）打日志返回空结构，不抛异常（与 F-04 同款降级）。
    如实口径：埋点只能向前生效——上线前的历史访问无法回溯，指标自上线起从 0 累积，
    不用任何其他表的时间戳伪造活跃度。
    """
    now = datetime.now()

    def _since(days: int) -> str:
        return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    empty = {"dau": 0, "wau": 0, "mau": 0, "daily": [], "total_users": 0,
             "total_logins": 0, "total_views": 0,
             "page_heat": [], "user_activity": [], "has_data": False}
    try:
        has_data = bool(_query_one("SELECT EXISTS (SELECT 1 FROM user_activity_log)")[0])
        if not has_data:
            return empty
        dau = _query_one(
            "SELECT COUNT(DISTINCT user_id) FROM user_activity_log WHERE created_at >= %s",
            (_since(1),))[0]
        wau = _query_one(
            "SELECT COUNT(DISTINCT user_id) FROM user_activity_log WHERE created_at >= %s",
            (_since(7),))[0]
        mau = _query_one(
            "SELECT COUNT(DISTINCT user_id) FROM user_activity_log WHERE created_at >= %s",
            (_since(30),))[0]
        daily = _query(
            "SELECT SUBSTRING(created_at FROM 1 FOR 10), COUNT(DISTINCT user_id), COUNT(*) "
            "FROM user_activity_log "
            "WHERE SUBSTRING(created_at FROM 1 FOR 10) >= %s "
            "GROUP BY 1 ORDER BY 1",
            ((now - timedelta(days=29)).strftime("%Y-%m-%d"),))
        total_users = _query_one("SELECT COUNT(*) FROM users")[0]
        total_logins = _query_one(
            "SELECT COUNT(*) FROM user_activity_log WHERE action = 'login'")[0]
        total_views = _query_one(
            "SELECT COUNT(*) FROM user_activity_log WHERE action = 'page_view'")[0]
        heat = _query(
            "SELECT page_path, COUNT(*) FROM user_activity_log "
            "WHERE created_at >= %s AND action = 'page_view' AND COALESCE(page_path, '') <> '' "
            "GROUP BY page_path ORDER BY COUNT(*) DESC, page_path",
            (_since(30),))
        users = _query(
            "SELECT user_id, MAX(user_name), MAX(role), COUNT(*), "
            "COUNT(DISTINCT SUBSTRING(created_at FROM 1 FOR 10)), MAX(created_at) "
            "FROM user_activity_log WHERE created_at >= %s "
            "GROUP BY user_id ORDER BY COUNT(*) DESC, user_id",
            (_since(30),))
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn) as e:
        print(f"[activity] 用户活跃数据表尚未迁移，活跃统计暂不可用：{e}")
        return empty
    return {
        "dau": int(dau), "wau": int(wau), "mau": int(mau),
        "daily": [{"date": r[0], "users": int(r[1]), "events": int(r[2])} for r in daily],
        "total_users": int(total_users),
        "total_logins": int(total_logins),
        "total_views": int(total_views),
        "page_heat": [(r[0], int(r[1])) for r in heat],
        "user_activity": [{
            "user_id": r[0], "user_name": r[1] or "", "role": r[2] or "",
            "visits": int(r[3]), "active_days": int(r[4]), "last_active": r[5] or "",
        } for r in users],
        "has_data": has_data,
    }
