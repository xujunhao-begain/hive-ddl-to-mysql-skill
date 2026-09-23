#!/bin/sh
# install.sh — 一键安装 hive-ddl-to-mysql-skill 到 Trae-CN 和/或 Claude Code
#
# 用法:
#   sh install.sh               # 自动检测环境，装到 global（检测到哪个装哪个，都检测到就都装）
#   sh install.sh --project     # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/skills/）
#   sh install.sh --trae        # 只装 Trae-CN（global）
#   sh install.sh --claude      # 只装 Claude Code（global）
#
# 安装布局:
#   Trae-CN     → 整目录装入 ~/.trae-cn/skills/hive-ddl-to-mysql-skill/（项目级为 <项目>/.trae/skills/）
#   Claude Code → 整目录装入 ~/.claude/skills/hive-ddl-to-mysql-skill/（项目级为 <项目>/.claude/skills/）
#   入口均为 SKILL.md，按 description 自动路由触发。
#
# copy_tree 用 tar 排除 .git / __pycache__ / config.yml / docs（产物）——
#   config.yml 含真实 MySQL 凭据、docs 是本地产物，都不入库也不带进 skill 目录，
#   由 check_config.py 引导本地准备。
#
# 装完自动跑一次 check_config.py 检测配置状态（退出码 2 = 缺 config.yml，3 = 配置不完整）。
# 注意：本技能【只生成建表语句时不需要 config.yml】，只有 --execute 真的连库建表才需要。

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NAME="hive-ddl-to-mysql-skill"
ONLY=""
MODE="global"

for arg in "$@"; do
  case "$arg" in
    --project) MODE="project" ;;
    --trae)    ONLY="trae" ;;
    --claude)  ONLY="claude" ;;
    *) echo "未知参数: $arg（支持 --project / --trae / --claude）" >&2; exit 2 ;;
  esac
done

# 注意：Trae-CN 的 global 目录是 ~/.trae-cn，但项目级是 <项目>/.trae（不是 .trae-cn）
if [ "$MODE" = "project" ]; then
  TRAE_ROOT="$PWD/.trae"
  CLAUDE_ROOT="$PWD/.claude"
else
  TRAE_ROOT="$HOME/.trae-cn"
  CLAUDE_ROOT="$HOME/.claude"
fi
TRAE_DEST="$TRAE_ROOT/skills/$NAME"
CLAUDE_DEST="$CLAUDE_ROOT/skills/$NAME"

# ---------------------------------------------------------------- 工具函数
real() { CDPATH= cd -- "$1" 2>/dev/null && pwd; }
is_same() { [ "$(real "$1")" = "$(real "$2")" ]; }

copy_tree() {
  # copy_tree <src> <dest> — 复制并排除 git/缓存/本机数据/产物
  mkdir -p "$2"
  (cd "$1" && tar \
       --exclude=.git --exclude=__pycache__ --exclude='*.pyc' \
       --exclude=.DS_Store \
       --exclude=config.yml \
       --exclude=docs \
       -cf - .) | (cd "$2" && tar -xf -)
}

safe_rmtree() {
  # safe_rmtree <dir> — 只删非空名字的合法路径，防变量为空导致误删
  case "$1" in
    "" | "/" | "$HOME") echo "拒绝删除可疑路径: '$1'" >&2; exit 2 ;;
    *) rm -rf "$1" ;;
  esac
}

# ---------------------------------------------------------------- 安装动作
install_one() {  # install_one <dest>
  if is_same "$SRC_DIR" "$1"; then
    echo "[源即目标] 跳过复制: $1"
  else
    safe_rmtree "$1"
    copy_tree "$SRC_DIR" "$1"
    echo "[已安装] $1"
  fi
  chmod +x "$1/scripts/"*.py 2>/dev/null
  chmod +x "$1/install.sh" 2>/dev/null
}

# ---------------------------------------------------------------- 决定装哪
DO_TRAE=0
DO_CLAUDE=0
if [ "$ONLY" = "trae" ]; then
  DO_TRAE=1
elif [ "$ONLY" = "claude" ]; then
  DO_CLAUDE=1
elif [ "$MODE" = "project" ]; then
  DO_TRAE=1                                # 项目级 trae 约定固定为 .trae/skills/
  [ -d "$CLAUDE_ROOT" ] && DO_CLAUDE=1
else
  [ -d "$TRAE_ROOT" ] && DO_TRAE=1
  [ -d "$CLAUDE_ROOT" ] && DO_CLAUDE=1
fi

if [ "$DO_TRAE" -eq 0 ] && [ "$DO_CLAUDE" -eq 0 ]; then
  echo "未检测到 Trae-CN（$HOME/.trae-cn）或 Claude Code（$HOME/.claude）环境。"
  echo "请显式指定: sh install.sh --trae 或 sh install.sh --claude"
  exit 1
fi

DETECT_DIR=""
if [ "$DO_TRAE" -eq 1 ]; then
  install_one "$TRAE_DEST"
  DETECT_DIR="$TRAE_DEST"
fi
if [ "$DO_CLAUDE" -eq 1 ]; then
  install_one "$CLAUDE_DEST"
  [ -z "$DETECT_DIR" ] && DETECT_DIR="$CLAUDE_DEST"
fi

# ---------------------------------------------------------------- 配置检测
echo ""
echo "=== 配置检测 ==="
if [ -n "$DETECT_DIR" ]; then
  if [ -x "$DETECT_DIR/scripts/check_config.py" ] || \
     [ -f "$DETECT_DIR/scripts/check_config.py" ]; then
    python3 "$DETECT_DIR/scripts/check_config.py" --skill-dir "$DETECT_DIR"
    RC=$?
    if [ "$RC" -eq 2 ] || [ "$RC" -eq 3 ]; then
      echo ""
      echo "需要首次配置（仅 --execute 真的连库建表时才需要）："
      echo "  1. cp $DETECT_DIR/config.example.yml $DETECT_DIR/config.yml"
      echo "  2. 在 config.yml 的 mysql 段填入 MySQL 连接信息（host/port/username/password/database）"
      echo "  3. 密码建议走环境变量 MYSQL_PASSWORD（如 ~/.claude/settings.json 的 env 段），不放 config.yml"
      echo "  4. 重跑: python3 \"$DETECT_DIR/scripts/check_config.py\" --skill-dir \"$DETECT_DIR\""
    fi
  else
    echo "[警告] $DETECT_DIR/scripts/check_config.py 不存在，跳过配置检测"
  fi
fi

exit 0
