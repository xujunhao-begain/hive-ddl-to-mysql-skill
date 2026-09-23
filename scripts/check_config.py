#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hive-ddl-to-mysql-skill 的配置检测与引导（Agent 友好，适配 trae-cn / claude）。

既作命令行工具（供 install.sh / Agent 判断是否需要首次配置引导），也作可复用模块。
配置由 config.yml 的 mysql: 段组成：host / port / username / password / database / charset。

重要：本技能【只生成建表语句时不需要 config.yml】——默认路径不连数据库。
只有 --execute 真的连库建表才会读 config.yml。本脚本检测的是"能否 --execute"的就绪状态。

密码来源（二选一即可，优先级 CLI > 环境变量 > config.yml，与 hive_ddl_to_mysql.py 一致）：
  1. config.yml 的 mysql.password（引导主路径，文件权限 600，不入库，trae-cn / claude 通用）；
  2. 环境变量 MYSQL_PASSWORD（Claude Code 可配在 ~/.claude/settings.json 的 env 段，
     改完需重开会话；Trae-CN 无等价的 settings 机制，推荐直接用方式 1）。

== 输出模式 ==
默认输出 JSON（机器可读，供 Agent 程序化消费）。
加 --human / --text 切回人本可读的纯文本。
--json 显式指定 JSON（与默认一致，便于脚本调用更明确）。

== 退出码（与输出模式无关）==
  0 = 就绪
  2 = 未配置（缺 config.yml 且 MYSQL_PASSWORD 也未提供）
  3 = 配置不完整（host/username/password 缺失或仍是占位值，或缺依赖 / 解析失败）

== JSON 形态 ==
{
  "ready": <bool>,
  "agent": "trae" | "claude" | "unknown",
  "skill_dir": "<abs path>",
  "config_path": "<abs path or null>",
  "config_exists": <bool>,
  "password_from_env": <bool>,
  "missing": [{"field": "host|username|password|config.yml|pyyaml", "reason": "..."}],
  "warnings": [...],
  "next_actions": [ ... ]
}

命令行用法:
    python3 check_config.py [--skill-dir <技能根目录>] [--human|--json]
"""
import argparse
import json
import os
import sys

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# config.example.yml 里的占位值，用来识别"还没改"的配置
PLACEHOLDER_HOST = "127.0.0.1"

# Agent 视角下需要"问用户"的连接字段（database 可空，仅告警；port/charset 有默认值）
CONNECTION_FIELDS = ("host", "username", "password")


def skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path_for(skill_dir):
    return os.path.join(skill_dir, "config.yml")


def template_path_for(skill_dir):
    return os.path.join(skill_dir, "config.example.yml")


def detect_agent(skill_dir):
    """按技能安装路径识别宿主 agent。

      ~/.claude/skills/<name>            -> claude
      <项目>/.claude/skills/<name>       -> claude
      ~/.trae-cn/skills/<name>           -> trae
      <项目>/.trae/skills/<name>         -> trae
    其它路径（如源码仓库里直接跑）-> unknown
    """
    parts = os.path.abspath(skill_dir).split(os.sep)
    if ".claude" in parts:
        return "claude"
    if ".trae-cn" in parts or ".trae" in parts:
        return "trae"
    return "unknown"


def password_from_env():
    """MYSQL_PASSWORD 环境变量是否已提供非空密码。"""
    return bool(os.environ.get("MYSQL_PASSWORD"))


def load_config(skill_dir):
    """返回 (cfg, err)。err 为 None 表示成功；否则为 (类型, 详情)。

    读 config.yml，取顶层 dict 的 mysql 段（兼容 MySQL 大写键），
    与 hive_ddl_to_mysql.py 的 `data.get("mysql")` 读法一致。
    """
    path = config_path_for(skill_dir)
    if not os.path.isfile(path):
        return None, ("NOT_CONFIGURED", path)
    if not HAS_YAML:
        return None, ("NO_PYYAML", "pip install pyyaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return None, ("INVALID", str(e))
    mysql = data.get("mysql") or data.get("MySQL") or {}
    if not isinstance(mysql, dict):
        return {}, None
    return mysql, None


# ---------------------------------------------------------------------------
# 缺失项检测
# ---------------------------------------------------------------------------

def check(skill_dir):
    """返回 (missing_items, warnings)。

    missing_items: list of {"field": str, "reason": str}
      field 取值: "host" / "username" / "password" / "config.yml" / "pyyaml"
    warnings: list of str
    """
    missing, warns = [], []
    cfg, err = load_config(skill_dir)
    if err:
        if err[0] == "NOT_CONFIGURED":
            # config.yml 不存在：host/username 必缺；password 若环境变量已给则不缺
            missing.append({"field": "host", "reason": "config.yml 不存在"})
            missing.append({"field": "username", "reason": "config.yml 不存在"})
            if not password_from_env():
                missing.append({"field": "password", "reason": "config.yml 不存在且未设 MYSQL_PASSWORD 环境变量"})
            return missing, warns
        if err[0] == "NO_PYYAML":
            missing.append({"field": "pyyaml", "reason": "缺 PyYAML（pip install pyyaml）"})
            return missing, warns
        missing.append({"field": "config.yml", "reason": f"解析失败: {err[1]}"})
        return missing, warns

    host = cfg.get("host")
    username = cfg.get("username") or cfg.get("user")
    database = cfg.get("database")

    if not host:
        missing.append({"field": "host", "reason": "mysql.host 为空"})
    elif host == PLACEHOLDER_HOST:
        # 与 config.example.yml 的占位一致：看起来没改过模板
        missing.append({"field": "host", "reason": f"mysql.host 仍是占位 {PLACEHOLDER_HOST}（请改成真实 MySQL 地址）"})

    if not username:
        missing.append({"field": "username", "reason": "mysql.username 为空"})

    # 密码：config.yml 与 MYSQL_PASSWORD 环境变量任一提供即可
    password = cfg.get("password")
    if not password and not password_from_env():
        missing.append({
            "field": "password",
            "reason": "mysql.password 为空且未设 MYSQL_PASSWORD 环境变量（--execute 连库建表需要密码）",
        })

    if not database:
        warns.append("mysql.database 为空（--execute 时需要目标库名，可用 --db 覆盖）")

    return missing, warns


def is_ready(skill_dir=None):
    """供模块调用：配置是否就绪（可 --execute）。"""
    skill_dir = skill_dir or skill_root()
    missing, _ = check(skill_dir)
    return not missing


# ---------------------------------------------------------------------------
# 构建 Agent 可执行的动作清单
# ---------------------------------------------------------------------------

def verify_cmd(skill_dir):
    return f"python3 {os.path.join(skill_dir, 'scripts', 'check_config.py')} --skill-dir {skill_dir}"


def _password_hint(agent):
    base = ("密码写入 config.yml 的 mysql.password（本地文件，写完 chmod 600，不入库）；"
            "trae-cn / claude 两个 agent 通用")
    if agent == "claude":
        return base + "。也可改走环境变量 MYSQL_PASSWORD（配在 ~/.claude/settings.json 的 env 段，改完重开会话生效）"
    if agent == "trae":
        return base + "。Trae-CN 无等价的 settings env 机制，建议直接写 config.yml"
    return base


def next_actions(skill_dir, missing, agent):
    """根据缺失项构建 Agent 可执行的动作清单（按推荐执行顺序）。"""
    if not missing:
        return []

    cfg_path = config_path_for(skill_dir)
    tpl_path = template_path_for(skill_dir)
    fields_missing = [m["field"] for m in missing]

    actions = []

    # 缺依赖 → 先装 PyYAML
    if "pyyaml" in fields_missing:
        actions.append({
            "step": "install_dep",
            "cmd": "pip install pyyaml",
            "reason": "缺 PyYAML 才能读取 config.yml",
        })

    # config.yml 不存在 / 解析失败 → 复制模板（或重写）
    needs_copy = (
        any("config.yml 不存在" in m["reason"] for m in missing)
        or "config.yml" in fields_missing
    )
    if needs_copy:
        actions.append({
            "step": "copy_template",
            "cmd": f"cp {tpl_path} {cfg_path}",
            "from": tpl_path,
            "to": cfg_path,
        })

    # 问用户要连接字段
    ask_fields = sorted({f for f in fields_missing if f in CONNECTION_FIELDS})
    if ask_fields:
        actions.append({
            "step": "ask_user",
            "fields": ask_fields,
            "hint": "向用户询问 MySQL 连接信息；不要猜，不要用占位值。" + _password_hint(agent)
                    if "password" in ask_fields
                    else "向用户询问 MySQL 连接信息；不要猜，不要用占位值",
        })
        actions.append({
            "step": "write_config",
            "path": cfg_path,
            "note": "按 config.example.yml 的 mysql: 段结构填入用户提供的值"
                    "（host/port/username/password/database/charset）",
        })

    # 锁权限（涉及连接字段或重写 config.yml 时）
    if any(f in (*CONNECTION_FIELDS, "config.yml") for f in fields_missing):
        actions.append({
            "step": "chmod",
            "cmd": f"chmod 600 {cfg_path}",
            "path": cfg_path,
            "mode": "600",
        })

    # 复跑验证
    actions.append({
        "step": "verify",
        "cmd": verify_cmd(skill_dir),
        "expect": "ready=true 即可 --execute 连库建表；仍 false 则按 missing 继续追问用户",
    })

    return actions


def status(skill_dir):
    """组装一个完整的状态 dict（供 JSON 输出）。"""
    missing, warns = check(skill_dir)
    cfg_path = config_path_for(skill_dir)
    cfg_exists = os.path.isfile(cfg_path)
    agent = detect_agent(skill_dir)
    return {
        "ready": not missing,
        "agent": agent,
        "skill_dir": os.path.abspath(skill_dir),
        "config_path": os.path.abspath(cfg_path) if cfg_exists else None,
        "config_exists": cfg_exists,
        "password_from_env": password_from_env(),
        "missing": missing,
        "warnings": warns,
        "next_actions": next_actions(skill_dir, missing, agent),
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def print_json(status_obj):
    print(json.dumps(status_obj, ensure_ascii=False, indent=2))


def print_human(status_obj):
    """人本可读的文本输出。"""
    skill_dir = status_obj["skill_dir"]
    agent = status_obj["agent"]
    cfg_path = status_obj["config_path"]
    missing = status_obj["missing"]
    warnings = status_obj["warnings"]

    if not missing:
        for w in warnings:
            print(f"[告警] {w}")
        print(f"[就绪] config.yml 的 MySQL 连接已填（{cfg_path}），可以加 --execute 连库建表")
        return

    # 有缺失项
    if not status_obj["config_exists"]:
        print(f"[未配置] 找不到 {config_path_for(skill_dir)}")
        print("引导: cp config.example.yml config.yml  然后填入 MySQL 连接信息")
        print("（只生成建表语句时不需要 config.yml，仅 --execute 连库建表时才需要）")
    else:
        print("[配置不完整] 以下项缺失/无效：")
        for m in missing:
            print(f"  - {m['field']}: {m['reason']}")
        print("\n引导:")
        print("  1. cp config.example.yml config.yml")
        print("  2. 在 config.yml 的 mysql 段填入 MySQL 连接（host/port/username/password/database）")
        print("  3. chmod 600 config.yml")
        print("  4. 重跑: python3 scripts/check_config.py")

    agent_note = {
        "claude": "密码也可走环境变量 MYSQL_PASSWORD（~/.claude/settings.json 的 env 段，改完重开会话生效）",
        "trae": "Trae-CN 无 settings env 机制，密码建议直接写 config.yml（权限 600，不入库）",
    }.get(agent)
    if agent_note:
        print(f"[agent={agent}] {agent_note}")


def main():
    ap = argparse.ArgumentParser(
        description="hive-ddl-to-mysql-skill 配置检测（默认 JSON，Agent 友好）"
    )
    ap.add_argument("--skill-dir", default=skill_root(),
                    help="技能根目录（默认: 脚本所在目录的上一级）")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--human", "--text", dest="human", action="store_true",
                     help="输出人本可读文本（默认是 JSON）")
    fmt.add_argument("--json", action="store_true",
                     help="显式指定 JSON 输出（与默认一致）")
    args = ap.parse_args()

    skill_dir = args.skill_dir or skill_root()
    status_obj = status(skill_dir)

    if args.human:
        print_human(status_obj)
    else:
        print_json(status_obj)

    # 退出码：ready → 0；缺 config.yml → 2；其它不完整 → 3
    if status_obj["ready"]:
        return 0
    if not status_obj["config_exists"]:
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())
