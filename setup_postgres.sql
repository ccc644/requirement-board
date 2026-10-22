-- ============================================================
-- 一次性初始化脚本：创建应用数据库和专用账号
-- ============================================================
-- 用法（用超级用户 postgres 执行，二选一）：
--   1) 命令行：
--      "D:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -f setup_postgres.sql
--   2) pgAdmin：新建查询，粘贴下面内容执行
--
-- 注意：密码改成你自己的，且要和 .env 里的 DB_PASSWORD 一致。

-- 1) 创建专用账号
CREATE ROLE demoapp WITH LOGIN PASSWORD '改成你的密码';

-- 2) 创建数据库（UTF8 编码，避免中文乱码）
--    如果数据库已存在，跳过这一步。
CREATE DATABASE kanban_demo
    OWNER demoapp
    ENCODING 'UTF8'
    TEMPLATE template0
    LC_COLLATE 'C'
    LC_CTYPE 'C';
