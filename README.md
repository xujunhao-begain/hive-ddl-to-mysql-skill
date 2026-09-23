# hive-ddl-to-mysql-skill

给一段 Hive / MaxCompute(ODPS) 的 `CREATE TABLE`，产出一条可直接执行的 MySQL `CREATE TABLE`，
每列保留原有的中文 `COMMENT`，并附一份字段映射核对报告。用户明确要求时才真的连库建表。

典型场景：「这张 Hive 表要同步到 MySQL，帮我把建表语句写出来」。

**输入是 DDL 文本，不连 Hive**——通过 `--ddl-file` / `--ddl` / stdin 传入。**默认只生成不执行**，
只有加 `--execute` 才连 MySQL 建表。完整行为细节见 [SKILL.md](SKILL.md)。

## 安装

### 一键安装（推荐）

```bash
git clone <repo-url> hive-ddl-to-mysql-skill
cd hive-ddl-to-mysql-skill
sh install.sh                 # 自动检测 trae-cn / claude，装到 global
# 或: sh install.sh --project # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/skills/）
# 或: sh install.sh --trae / --claude        # 只装其中一个
# 或: sh install.sh --non-interactive        # Agent 模式：装完输出 ---BEGIN/END CONFIG JSON--- 状态块
```

`install.sh` 会把整目录复制到：

- Trae-CN：`~/.trae-cn/skills/hive-ddl-to-mysql-skill/`（项目级为 `<项目>/.trae/skills/`）
- Claude Code：`~/.claude/skills/hive-ddl-to-mysql-skill/`（项目级为 `<项目>/.claude/skills/`）

安装时用 tar 排除 `config.yml`、`docs/`——前者含真实 MySQL 凭据、后者是本地产物，
都不会带进 skill 目录；重装/升级时目标目录已有的 `config.yml`、`docs/` 会自动备份并原样移回。

装完自动跑 `check_config.py` 检测配置状态：

- **真人终端（交互式 TTY）** 且缺配置时，自动进入 `scripts/setup_config.py` 逐项向导
  （密码无回显，写完 chmod 600 并复验）；`--no-configure` 可跳过，`--configure` 强制重配。
- **非 TTY（脚本 / 管道 / Agent 子进程）** 不挂起，打印可照做的动作清单。
- **`--non-interactive`（同 `--yes` / `--json`）** 输出机器可读 JSON，
  用 `---BEGIN CONFIG JSON---` / `---END CONFIG JSON---` 包起来，供 Agent 截取消费。

**只生成建表语句不需要 config.yml**，缺配置不算安装失败。

### 依赖

```bash
pip install -r requirements.txt   # PyYAML + PyMySQL（仅 --execute 连库时才需要 PyMySQL）
```

## 配置（仅 --execute 连库建表需要）

**只生成建表语句不需要 config.yml**——这是默认路径，直接就能跑。
只有 `--execute` 真的连库建表才需要 MySQL 连接信息。复制配置模板并填入自己的连接：

```bash
cp config.example.yml config.yml
# 或在终端直接跑交互式向导：
python3 scripts/setup_config.py
```

`config.yml` 不入库（含真实凭据，建议 `chmod 600`）。
`mysql` 段字段：`host / port / username / password / database / charset`。

**密码主路径：直接写 `config.yml` 的 `mysql.password`（chmod 600，不入库），trae-cn / claude 通用。**
可选替代是环境变量 `MYSQL_PASSWORD`（Claude Code 可配在 `~/.claude/settings.json` 的 `env` 段，
改动后需重开会话生效；Trae-CN 无等价 settings 机制，建议直接用 config.yml）；
也可临时用 `--password` 覆盖。优先级 **CLI > 环境变量 > config.yml**。

完成配置后用 `check_config.py` 验证（**默认输出 JSON**，加 `--human` 看纯文本）：

```bash
python3 scripts/check_config.py --json    # Agent 消费：ready / agent / missing / next_actions
python3 scripts/check_config.py --human   # 人看
# 退出码 0 = 就绪，2 = 缺 config.yml，3 = 配置不完整（列出缺失项）
```

JSON 里的 `agent` 字段按安装路径自动识别（`trae` / `claude`），`next_actions`
给出当前 agent 可照做的配置步骤；`missing` 会同时检查 config.yml 与 `MYSQL_PASSWORD`
环境变量两种密码来源。`install.sh` 装完会自动跑一次，按提示补齐即可。

## 用法

```bash
# ① 主路径：Hive DDL 落成文件再转（推荐，DDL 通常很长且带引号换行）
python3 scripts/hive_ddl_to_mysql.py --ddl-file /tmp/hive_ddl.sql

# ② DDL 很短时直接传
python3 scripts/hive_ddl_to_mysql.py --ddl "create table db.t (a string comment '甲')"

# ③ 用户明确说「建表 / 执行」时才加 --execute
python3 scripts/hive_ddl_to_mysql.py --ddl-file d.sql --execute --db mydb
python3 scripts/hive_ddl_to_mysql.py --ddl-file d.sql --execute --create-database --db newdb

# ④ 改表名/库名、加主键和索引
python3 scripts/hive_ddl_to_mysql.py --ddl-file d.sql \
  --table t_new --db mydb --primary-key order_no --index user_id,dt --not-null user_id

# ⑤ 不想要字段名启发式，所有 string 统一给一个类型
python3 scripts/hive_ddl_to_mysql.py --ddl-file d.sql --string-type 'VARCHAR(512)'

# ⑥ 只有 describe 输出、没有建表语句时
python3 scripts/hive_ddl_to_mysql.py --format describe --ddl-file cols.txt --table t
```

完整参数见 `python3 scripts/hive_ddl_to_mysql.py -h`。

产物落在 `docs/hive-ddl-to-mysql/`：

- `<表名>_<时间戳>.sql` —— MySQL 建表语句（头部注释记录生成时间、列数、告警数、是否已执行）
- `<表名>_<时间戳>_字段核对报告.md` —— 字段映射明细表 + 需人工确认的启发式类型 + 全部告警 + 完整 DDL
- `<表名>_<时间戳>_字段映射.csv` —— 同样的映射表，方便贴进表格评审

跑完务必过一遍**按字段名猜出来的 varchar 长度**——这是唯一会静默截断生产数据的一档。

## 边界

- **`string` → 长度是猜的，猜短了会截断数据**。字段名启发式只是省事，不是权威依据；拿不准就 `--string-type TEXT`。
- **不做数据迁移**，只管表结构。搬数据是另一回事（导出可用 `hive-sql-to-csv`）。
- **表已存在时不改结构**。默认 `IF NOT EXISTS` 只保证不报错，不会 ALTER 对齐字段。
- **`array/map/struct` → JSON 需要上游配合**：MySQL 的 JSON 列要求写入合法 JSON 文本，Hive 侧 toString 出来的格式通常不是合法 JSON。
- 不查字段字典补中文注释（那是 `dataworks-sql-table` 的 `ddl` 子命令）；
  不在 Hive 上执行 SQL（那是 `hive-sql-to-csv`）；不追字段血缘（那是 `sql-field-lineage`）。

## License

MIT
