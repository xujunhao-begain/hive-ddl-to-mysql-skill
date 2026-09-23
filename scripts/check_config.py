#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hive-ddl-to-mysql-skill 的配置检测与引导。

既作命令行工具（供 install.sh / agent 判断是否需要首次配置引导），也作可复用模块。
配置文件 config.yml 的 mysql 段存放 MySQL 连接信息（host/port/username/password/database/charset）。

重要：本技能【只生成建表语句时不需要 config.yml】——默认路径不连数据库。
只有 --execute 真的连库建表才会读 config.yml。密码建议走环境变量 MYSQL_PASSWORD，
不放 config.yml。config.yml 不入库（含真实凭据），由 config.example.yml 作模板。

退出码:
  0 = 就绪（config.yml 存在、mysql 段有 host/username 等关键字段、host 不是占位 127.0.0.1）
  2 = 未配置（缺 config.yml）
  3 = 配置不完整（缺 mysql 段、host/username 为空、或 host 仍是占位 127.0.0.1）

命令行用法:
    python3 check_config.py [--skill-dir <技能根目录>]
"""
import argparse
import os
import sys

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(skill_dir):
    """返回 (cfg, err)。err 为 None 表示成功；否则为 (类型, 详情)。

    读 config.yml，取顶层 dict。mysql 段的提取放到 check() 里做，与
    hive_ddl_to_mysql.py 的 `data.get("mysql")` 读法一致。
    """
    path = os.path.join(skill_dir, "config.yml")
    if not os.path.isfile(path):
        return None, ("NOT_CONFIGURED", path)
    if not HAS_YAML:
        return None, ("NO_PYYAML", "pip install pyyaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}, None
    except Exception as e:
        return None, ("INVALID", str(e))


def check(skill_dir):
    """返回 (缺失项列表, 告警列表)。缺失项非空 -> 退出码 3。

    检查 mysql 段是否齐全：host / username 必填且 host 不能仍是占位 127.0.0.1
    （直接 cp config.example.yml 未改的样子）；database 缺失只告警（可用 --db 覆盖）。
    """
    missing, warns = [], []
    cfg, err = load_config(skill_dir)
    if err:
        if err[0] == "NOT_CONFIGURED":
            return ["config.yml（复制 config.example.yml 后填 MySQL 连接）"], warns
        if err[0] == "NO_PYYAML":
            return ["PyYAML（pip install pyyaml）"], warns
        return [f"config.yml 解析失败: {err[1]}"], warns

    mysql = cfg.get("mysql") or cfg.get("MySQL") or {}
    if not isinstance(mysql, dict) or not mysql:
        missing.append("config.yml 的 mysql 段（host/port/username/password/database/charset）")
        return missing, warns

    host = mysql.get("host")
    username = mysql.get("username") or mysql.get("user")
    database = mysql.get("database")

    if not host:
        missing.append("mysql.host（为空）")
    elif host == "127.0.0.1":
        # 与 config.example.yml 的占位一致：看起来没改过模板
        missing.append("mysql.host 仍是占位 127.0.0.1（请改成真实 MySQL 地址）")

    if not username:
        missing.append("mysql.username（为空）")

    if not database:
        warns.append("mysql.database 为空（--execute 时需要目标库名，可用 --db 覆盖）")

    return missing, warns


def is_ready(skill_dir=None):
    """供模块调用：配置是否就绪。"""
    skill_dir = skill_dir or skill_root()
    missing, _ = check(skill_dir)
    return not missing


def main():
    ap = argparse.ArgumentParser(description="hive-ddl-to-mysql-skill 配置检测")
    ap.add_argument("--skill-dir", default=skill_root(),
                    help="技能根目录（默认: 脚本所在目录的上一级）")
    args = ap.parse_args()

    cfg, err = load_config(args.skill_dir)
    if err and err[0] == "NOT_CONFIGURED":
        print(f"[未配置] 找不到 {err[1]}")
        print("引导: cp config.example.yml config.yml  然后填入 MySQL 连接信息")
        print("（只生成建表语句时不需要 config.yml，仅 --execute 连库建表时才需要）")
        return 2
    if err and err[0] == "NO_PYYAML":
        print(f"[缺依赖] {err[1]}")
        return 3

    missing, warns = check(args.skill_dir)
    if missing:
        print("[配置不完整] 以下项缺失/无效：")
        for m in missing:
            print(f"  - {m}")
        print("\n引导:")
        print("  1. cp config.example.yml config.yml")
        print("  2. 在 config.yml 的 mysql 段填入 MySQL 连接（host/port/username/password/database）")
        print("  3. 密码建议走环境变量 MYSQL_PASSWORD，不放 config.yml")
        print("  4. 重跑: python3 scripts/check_config.py")
        return 3

    for w in warns:
        print(f"[告警] {w}")
    print("[就绪] config.yml 的 mysql 段已配置，可以加 --execute 连库建表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
