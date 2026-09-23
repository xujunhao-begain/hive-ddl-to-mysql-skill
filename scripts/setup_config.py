#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hive-ddl-to-mysql-skill 的交互式配置向导。

在终端里逐项向使用者询问 MySQL 连接信息（host / port / username / password /
database / charset），写入 <技能目录>/config.yml 的 mysql: 段并 chmod 600，
最后复跑 check_config 验证。供 install.sh 在交互式 TTY 下自动调用，也可单独运行：

    python3 scripts/setup_config.py                 # config.yml 缺失或不完整时向导
    python3 scripts/setup_config.py --force         # 已有可用配置也重走一遍（覆盖）
    python3 scripts/setup_config.py --skill-dir X   # 指定技能目录

只服务于 --execute 连库建表：只生成建表语句不需要 config.yml，可随时跳过本向导。

设计约束：
- 只在交互式 TTY 下使用；非 TTY（管道 / Agent 子进程）直接退出码 4，避免挂住。
  Agent 场景应由 Agent 自己在对话里问用户、用 Write 写 config.yml（见 SKILL.md）。
- 密码用 getpass 无回显输入；不回显、不打印到日志。
- 已设置 MYSQL_PASSWORD 环境变量时密码项可留空沿用（环境变量优先于 config.yml）。
- 退出码：0 = 就绪；3 = 写完仍不完整（理论上不该发生）；4 = 非 TTY，无法交互；
  5 = 用户在确认环节放弃。
"""
import argparse
import getpass
import os
import stat
import sys

# 复用同目录 check_config 的检测逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_config  # noqa: E402

PLACEHOLDER_HOST = check_config.PLACEHOLDER_HOST


def ask(prompt, default=None, secret=False, allow_empty=False):
    """通用提问。secret=True 时无回显；空输入且有 default 取 default；
    allow_empty=True 时空输入返回空串；否则必填重问。"""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        if secret:
            val = getpass.getpass(f"{prompt}{suffix}（输入无回显）: ")
        else:
            val = input(f"{prompt}{suffix}: ").strip()
        if not val and default not in (None, ""):
            return str(default)
        if val:
            return val
        if allow_empty:
            return ""
        print("  ! 该项必填，请重新输入。")


def ask_yes_no(prompt, default_no=True):
    d = "y/N" if default_no else "Y/n"
    val = input(f"{prompt} [{d}]: ").strip().lower()
    if not val:
        return not default_no
    return val in ("y", "yes", "是")


def collect_values(existing=None):
    """逐项收集连接参数。existing 为已有的 mysql 配置 dict（可回车沿用）。"""
    existing = existing or {}
    env_password_set = check_config.password_from_env()

    print("")
    print("=== MySQL 连接配置向导 ===")
    print("（回车沿用方括号里的现有值；密码输入时不回显）")
    print("（本配置仅 --execute 真的连库建表时需要；只生成语句可直接 Ctrl-C 跳过）")
    print("")

    while True:
        cur_host = existing.get("host")
        host = ask("MySQL 地址 host",
                   default=cur_host if cur_host not in (None, PLACEHOLDER_HOST) else None)
        if host and host != PLACEHOLDER_HOST:
            break
        print(f"  ! host 不能为占位值 {PLACEHOLDER_HOST}，请填真实地址。")

    port = ask("端口 port", default=existing.get("port", 3306))
    try:
        port = int(port)
    except ValueError:
        sys.exit(f"端口必须是整数，收到: {port!r}")

    username = ask("账号 username", default=existing.get("username") or None)

    # 密码：config.yml 现有值可沿用；否则若 MYSQL_PASSWORD 已设可留空走环境变量；再否则必填
    cur_pwd = existing.get("password")
    if cur_pwd:
        password = ask("密码 password（留空沿用现有值；新输入则覆盖）",
                       secret=True, allow_empty=True) or cur_pwd
    elif env_password_set:
        password = ask("密码 password（环境变量 MYSQL_PASSWORD 已设置，"
                       "留空即走环境变量；输入则写入 config.yml）",
                       secret=True, allow_empty=True)
    else:
        while True:
            password = ask("密码 password", secret=True)
            if password:
                break
            print("  ! 密码必填（或先 Ctrl-C 退出，export MYSQL_PASSWORD 后再跑）。")

    database = ask("目标库 database（可留空，用时再 --db 指定）",
                   default=existing.get("database") or "", allow_empty=True)

    charset = ask("字符集 charset", default=existing.get("charset", "utf8mb4"))

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "database": database,
        "charset": charset,
    }


def write_config(skill_dir, values):
    """写入 config.yml（mysql: 段）并 chmod 600。"""
    try:
        import yaml
    except ImportError:
        sys.exit("需要 PyYAML 才能写 config.yml：pip install pyyaml")

    path = check_config.config_path_for(skill_dir)
    payload = {"mysql": values}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600
    return path


def main():
    ap = argparse.ArgumentParser(description="hive-ddl-to-mysql-skill 交互式配置向导")
    ap.add_argument("--skill-dir", default=check_config.skill_root(),
                    help="技能根目录（默认: 脚本所在目录的上一级）")
    ap.add_argument("--force", action="store_true",
                    help="已有可用配置也重走向导（覆盖 config.yml）")
    args = ap.parse_args()

    skill_dir = args.skill_dir or check_config.skill_root()

    # 非交互环境直接退出，绝不挂住调用方
    if not sys.stdin.isatty():
        print("[跳过向导] 当前不是交互式 TTY（stdin 已被重定向）。", file=sys.stderr)
        print("Agent 场景：请在对话里向用户询问 host/username/password 后用 Write 写 config.yml，", file=sys.stderr)
        print("或让用户在终端手动运行: python3 scripts/setup_config.py", file=sys.stderr)
        return 4

    cfg_path = check_config.config_path_for(skill_dir)
    already_ready = check_config.is_ready(skill_dir)

    if already_ready and not args.force:
        print(f"[已就绪] {cfg_path} 已配置完成。")
        if not ask_yes_no("要重新配置并覆盖吗？", default_no=True):
            print("保持现有配置，退出。")
            return 0
        # 回答“是” → 继续往下走，以现有配置为默认值重走一遍向导

    # 读现有值做默认值（config.yml 存在但不完整 / --force 时）
    existing = {}
    if os.path.isfile(cfg_path):
        cfg, err = check_config.load_config(skill_dir)
        if not err:
            existing = cfg or {}

    values = collect_values(existing)

    # 落笔前确认（不回显密码）
    print("")
    print("即将写入:")
    print(f"  path     : {cfg_path}（权限 600）")
    print(f"  host     : {values['host']}")
    print(f"  port     : {values['port']}")
    print(f"  username : {values['username']}")
    print(f"  database : {values['database'] or '（空，用时 --db 指定）'}")
    print(f"  charset  : {values['charset']}")
    print(f"  password : {'*' * 8}（不回显）")
    if not ask_yes_no("确认写入？", default_no=False):
        print("已放弃，未改动任何文件。")
        return 5

    path = write_config(skill_dir, values)
    print(f"\n[已写入] {path}（chmod 600）")

    missing, _ = check_config.check(skill_dir)
    if missing:
        print("[警告] 写入后检测仍有缺失项：", file=sys.stderr)
        for m in missing:
            print(f"  - {m['field']}: {m['reason']}", file=sys.stderr)
        return 3

    print("[就绪] 配置完成，可以加 --execute 连库建表了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
