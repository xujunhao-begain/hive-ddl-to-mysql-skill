#!/bin/sh
# install.sh — 一键安装 hive-ddl-to-mysql-skill 到 Trae-CN 和/或 Claude Code
#
# 用法:
#   sh install.sh               # 自动检测环境，装到 global（检测到哪个装哪个，都检测到就都装）
#   sh install.sh --project     # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/skills/）
#   sh install.sh --trae        # 只装 Trae-CN（global）
#   sh install.sh --claude      # 只装 Claude Code（global）
#   sh install.sh --non-interactive   # Agent 模式：装完跑 check_config.py --json，最后输出一段 JSON 给 Agent 消费
#   sh install.sh --yes        # 同 --non-interactive
#   sh install.sh --json       # 同 --non-interactive（显式指定 JSON 输出）
#
# 配置向导（仅交互模式生效）:
#   装完若 config.yml 缺失/不完整，且当前是交互式 TTY，自动进入 setup_config.py
#   逐项向导（host/port/username/password/database/charset），写完 chmod 600 并复验。
#   sh install.sh --no-configure  # 装完不进向导，只打印动作清单
#   sh install.sh --configure     # 已就绪也强制重走向导（覆盖 config.yml）
#   非 TTY（脚本/管道/Agent 子进程）不会挂起：自动退化为打印动作清单。
#
# 安装布局:
#   Trae-CN     → 整目录装入 ~/.trae-cn/skills/hive-ddl-to-mysql-skill/（项目级为 <项目>/.trae/skills/）
#   Claude Code → 整目录装入 ~/.claude/skills/hive-ddl-to-mysql-skill/（项目级为 <项目>/.claude/skills/）
#   入口均为 SKILL.md，按 description 自动路由触发。
#
# copy_tree 用 tar 排除 .git / __pycache__ / config.yml / docs（产物）——
#   config.yml 含真实 MySQL 凭据、docs 是本地产物，都不入库也不带进 skill 目录。
#   目标目录里已有的 config.yml / docs/ 会在重装/升级前挪开、装完原样移回，不丢失。
#
# 装完自动跑一次 check_config.py 检测配置状态（退出码 0=就绪 2=缺 config.yml 3=不完整）：
# - 交互式 TTY 且缺配置：自动进入 setup_config.py 向导，当场问完写好（chmod 600）并复验；
# - 非 TTY（脚本/管道/Agent 子进程）：打印 Agent 可照做的"动作清单"，不挂起；
# - --non-interactive / --yes / --json：输出 JSON（---BEGIN/END CONFIG JSON---）给 Agent 消费。
#
# 注意：本技能【只生成建表语句时不需要 config.yml】，只有 --execute 真的连库建表才需要。
# 因此缺配置不算安装失败，安装部分始终成功。

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NAME="hive-ddl-to-mysql-skill"
ONLY=""
MODE="global"
JSON_MODE=0
NO_CONFIGURE=0
FORCE_CONFIGURE=0

for arg in "$@"; do
  case "$arg" in
    --project) MODE="project" ;;
    --trae)    ONLY="trae" ;;
    --claude)  ONLY="claude" ;;
    --non-interactive|--yes|--json) JSON_MODE=1 ;;
    --no-configure) NO_CONFIGURE=1 ;;
    --configure)    FORCE_CONFIGURE=1 ;;
    *) echo "未知参数: ${arg}（支持 --project / --trae / --claude / --non-interactive / --yes / --json / --no-configure / --configure）" >&2; exit 2 ;;
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
    # 重装/升级前把本机数据挪开，装完原样移回——绝不抹掉 config.yml / docs/
    BK=$(mktemp -d 2>/dev/null || mktemp -d -t traemysqlskill)
    HAD_BK=0
    for item in config.yml docs; do
      if [ -e "$1/$item" ]; then
        mv "$1/$item" "$BK/" && HAD_BK=1
      fi
    done
    safe_rmtree "$1"
    copy_tree "$SRC_DIR" "$1"
    if [ "$HAD_BK" -eq 1 ]; then
      for item in config.yml docs; do
        [ -e "$BK/$item" ] && mv "$BK/$item" "$1/"
      done
      echo "[已保留本机数据] config.yml / docs/（若存在）"
    fi
    rmdir "$BK" 2>/dev/null
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
  echo "未检测到 Trae-CN（$HOME/.trae-cn）或 Claude Code（$HOME/.claude）环境。" >&2
  echo "请显式指定: sh install.sh --trae 或 sh install.sh --claude" >&2
  exit 1
fi

INSTALLED_DIRS=""
if [ "$DO_TRAE" -eq 1 ]; then
  install_one "$TRAE_DEST"
  INSTALLED_DIRS="$INSTALLED_DIRS $TRAE_DEST"
  DETECT_DIR="$TRAE_DEST"
fi
if [ "$DO_CLAUDE" -eq 1 ]; then
  install_one "$CLAUDE_DEST"
  INSTALLED_DIRS="$INSTALLED_DIRS $CLAUDE_DEST"
  [ -z "$DETECT_DIR" ] && DETECT_DIR="$CLAUDE_DEST"
fi

# ---------------------------------------------------------------- 配置检测
# 默认（交互模式 + TTY）: 缺配置时直接进入 setup_config.py 交互向导，当场问完写好。
# 交互模式但非 TTY（脚本/管道/Agent 子进程）: 打印 Agent 也可照做的「动作清单」，绝不挂起。
# --non-interactive / --yes / --json: 调 check_config.py（JSON），
#   用 ---BEGIN CONFIG JSON--- / ---END CONFIG JSON--- 包起来，Agent 按这两个标记截取。
# 两个 agent 都装时，各副本的 config.yml 互相独立，JSON/向导只引导 DETECT_DIR（优先 trae）那一份；
# 另一份可直接 cp 同一份 config.yml 过去（chmod 600）。

print_action_list() {
  echo ""
  echo "=== 下一步动作（按顺序执行，Agent 可直接照做） ==="
  echo "  1. cp $DETECT_DIR/config.example.yml $DETECT_DIR/config.yml"
  echo "  2. 询问用户的 MySQL 连接信息：host / port(默认 3306) / username / password / database / charset(默认 utf8mb4)"
  echo "     （不要猜，不要用占位值 127.0.0.1；密码写本地 config.yml，权限 600，不入库）"
  echo "  3. 把上述值写入 $DETECT_DIR/config.yml 的 mysql: 段"
  echo "  4. chmod 600 $DETECT_DIR/config.yml"
  echo "  5. python3 \"$DETECT_DIR/scripts/check_config.py\" --skill-dir \"$DETECT_DIR\" --human"
  echo ""
  echo "提示：只生成建表语句不需要 config.yml，仅 --execute 连库建表才需要以上配置。"
  if [ "$DO_TRAE" -eq 1 ] && [ "$DO_CLAUDE" -eq 1 ]; then
    echo "两个 agent 都已安装：另一份副本可直接 'cp $DETECT_DIR/config.yml <另一份目录>/config.yml'（chmod 600）。"
  fi
  echo "（也可在终端直接跑向导: python3 \"$DETECT_DIR/scripts/setup_config.py\"）"
  echo "（Agent 模式可改用: sh install.sh --non-interactive  或  check_config.py --json 拿机器可读状态）"
}

if [ "$JSON_MODE" -eq 1 ]; then
  echo "---BEGIN CONFIG JSON---"
  python3 "$DETECT_DIR/scripts/check_config.py" --skill-dir "$DETECT_DIR" --json
  JSON_RC=$?
  echo "---END CONFIG JSON---"
  if [ "$DO_TRAE" -eq 1 ] && [ "$DO_CLAUDE" -eq 1 ]; then
    echo "NOTE: 同时安装了 trae-cn 与 claude 两份副本，以上为 $DETECT_DIR 的状态；" >&2
    echo "      另一份复制同一份 config.yml 即可（chmod 600）。" >&2
  fi
  # 缺配置不算安装失败（只生成语句不需要），Agent 模式安装步骤本身始终成功退出
  exit 0
fi

echo ""
echo "=== 配置检测 ==="
if [ ! -f "$DETECT_DIR/scripts/check_config.py" ]; then
  echo "[警告] $DETECT_DIR/scripts/check_config.py 不存在，跳过配置检测" >&2
  exit 0
fi

python3 "$DETECT_DIR/scripts/check_config.py" --skill-dir "$DETECT_DIR" --human
RC=$?

# 已就绪且未要求重配 → 结束
if [ "$RC" -eq 0 ] && [ "$FORCE_CONFIGURE" -eq 0 ]; then
  exit 0
fi

# 显式跳过向导
if [ "$NO_CONFIGURE" -eq 1 ]; then
  [ "$RC" -ne 0 ] && print_action_list
  exit 0
fi

# 交互式 TTY → 进入向导（当场逐项提问、写 config.yml、chmod 600、复验）
if [ -t 0 ] && [ -f "$DETECT_DIR/scripts/setup_config.py" ]; then
  echo ""
  echo "=== 进入交互式配置向导（仅 --execute 连库建表需要，可随时 Ctrl-C 跳过） ==="
  WIZ_ARGS="--skill-dir $DETECT_DIR"
  [ "$FORCE_CONFIGURE" -eq 1 ] && WIZ_ARGS="$WIZ_ARGS --force"
  # shellcheck disable=SC2086
  python3 "$DETECT_DIR/scripts/setup_config.py" $WIZ_ARGS
  WIZ_RC=$?
  # 向导放弃(5)/未完成(3) → 仍给出手动动作清单兜底
  if [ "$WIZ_RC" -ne 0 ] && [ "$RC" -ne 0 ]; then
    print_action_list
  fi
  exit 0
fi

# 非 TTY（被管道/脚本/Agent 调起）→ 不挂起，给动作清单
if [ "$RC" -ne 0 ]; then
  echo ""
  echo "[提示] 当前非交互式终端，无法弹配置向导。"
  print_action_list
fi

exit 0
