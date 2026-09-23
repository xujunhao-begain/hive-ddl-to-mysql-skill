---
name: hive-ddl-to-mysql-skill
description: 把 Hive / MaxCompute(ODPS) 的 CREATE TABLE 语句转成 MySQL 建表语句，并可按要求直接连 MySQL 执行建表。当用户说「把这个 hive 建表语句转成 mysql 的」「这张 hive 表在 mysql 里怎么建」「hive 表结构同步到 mysql」「按这个 DDL 在 mysql 建张表」「帮我在 mysql 里建这张表」「Hive DDL 转 MySQL DDL」时使用。自动做类型映射（string→varchar/text、decimal 保精度、boolean→tinyint(1)、array/map/struct→json）、保留每列中文 COMMENT、反引号包裹保留字、把 Hive 分区字段降级成普通列。默认【只生成语句不动数据库】，只有用户明确要求建表时才加 --execute 连库执行；连接信息从 config.yml 的 mysql 段读取。用户说「安装并配置 hive-ddl-to-mysql-skill」「先把这个 skill 装上」「配置 mysql 连接 / 初始化 config.yml / 第一次使用前设置 mysql 账号密码 / 配一下 mysql 的 host 和密码」时也使用：用 install.sh 一键安装到 trae-cn 和/或 claude（自动识别路径、--non-interactive 输出 JSON），跑 check_config.py 拿到 missing/next_actions，需要 --execute 时当场问齐 host/username/password 并写入 config.yml（chmod 600，不入库），配置引导在同一轮对话内完成，不能只丢一句"首次使用前请自行配置"就收尾；只生成语句不需要配置、装完即可用。同时产出字段核对报告，把按字段名猜出来的 varchar 长度单列出来供复核。不负责在 Hive 上跑 SQL 导出数据（那是 hive-sql-to-csv），不负责查字段字典补中文注释生成 ODPS 建表语句（那是 dataworks-sql-table 的 ddl 子命令），也不负责追字段血缘（那是 sql-field-lineage）。
version: 1.1.0
---

# hive-ddl-to-mysql-skill

给一段 Hive/ODPS 的 `CREATE TABLE`，产出一条可直接执行的 MySQL `CREATE TABLE`，
每列保留原有的中文 `COMMENT`，并附一份字段映射核对报告。用户明确要求时才真的连库建表。

典型场景：「这张 Hive 表要同步到 MySQL，帮我把建表语句写出来」。

这件事的难点不在改几个类型名，而在**Hive 的类型系统比 MySQL 松**，转换必然要做取舍：

- `string` 没有长度，MySQL 必须选一个具体类型——**猜短了会截断数据**。
- `array/map/struct` 在 MySQL 没有对应物。
- Hive 的**分区字段**是独立概念，MySQL 的分区完全是另一回事。

所以脚本把每个取舍都记进报告，并把启发式推断的部分**单列出来**，而不是混在结果里假装确定。

## Agent 一键安装与配置引导（trae-cn / claude）

当 Agent（Trae-CN / Claude）被要求"**安装本 skill**"或"**安装并配置好 MySQL 连接**"时，
按下面 7 步走。整个流程**对 Agent 是自洽的**——不需要再去翻文档，也不需要用户手把手指导。

> **硬性规则（最重要）**
>
> 1. **安装永远成功、配置按需进行**：本技能只生成建表语句时**不需要任何配置**。
>    用户只要求"安装"时，装完即可用，**不要**强迫用户提供 MySQL 凭据。
> 2. 用户明确要求"**安装并配置**"，或接下来要用 `--execute` 连库建表时，配置引导必须
>    **在同一轮对话内完成**：跑 `check_config.py`、按 `missing` 当场问齐缺失字段、
>    写好 `config.yml`（chmod 600）、复验到 `ready=true`。**禁止**用"首次使用前请参考
>    config.example.yml 自行配置"这类话收尾——那等于没做完。
> 3. 用户给不出凭据时可以停下等他提供，但要明确告诉他"在给出 host/账号/密码前无法
>    `--execute`，但只转 DDL 不受影响"，而不是默默结束。
> 4. **通用安装器兜底**：Trae/Claude 的通用 skill 安装器可能只复制文件、不执行本仓库的
>    `install.sh`（表现为安装报告里没有 `---BEGIN CONFIG JSON---` 段）。这种情况下，
>    Agent 在安装报告后**必须自己从第 2 步接着做**：定位安装目录、跑 `check_config.py`、
>    问用户、写配置。
> 5. Agent 自己执行 Shell 时**不要**跑 `setup_config.py` 向导（子进程没有 TTY，会直接
>    退出码 4）。Agent 应在对话里问用户、用 Write 写文件；向导只给真人终端用。

### 1. 一键安装到目标环境

`<REPO>` 是仓库克隆/解压后的根目录（含 `install.sh`、`SKILL.md`、`scripts/`）。

```bash
sh <REPO>/install.sh                       # 自动检测 trae-cn / claude，装到 global；都检测到就都装
# 或显式指定其一：
sh <REPO>/install.sh --trae                 # 只装 Trae-CN（global）
sh <REPO>/install.sh --claude               # 只装 Claude Code（global）
sh <REPO>/install.sh --project              # 装到当前项目（<项目>/.trae/skills 与 <项目>/.claude/skills）
```

**Agent 推荐**加 `--non-interactive`（等价于 `--yes` / `--json`）：装完不打印人类步骤提示，
改为在末尾输出一段 JSON（用 `---BEGIN CONFIG JSON---` / `---END CONFIG JSON---` 包起来），
Agent 按这两个标记截取即可。

```bash
sh <REPO>/install.sh --non-interactive      # Agent 模式：装完输出 JSON
```

> 注意：`install.sh` 在**真人终端**（交互式 TTY）里跑、且配置缺失时，会自动进入
> `scripts/setup_config.py` 向导逐项提问；Agent 经 Shell 调起时 stdin 不是 TTY，不会挂起，
> 会退化为输出动作清单 / JSON——此时配置由 Agent 在对话里完成（第 3 步）。

安装位置（自动检测到的每个 agent 各装一份）：

- Trae-CN：`~/.trae-cn/skills/hive-ddl-to-mysql-skill/`（项目级为 `<项目>/.trae/skills/`）
- Claude Code：`~/.claude/skills/hive-ddl-to-mysql-skill/`（项目级为 `<项目>/.claude/skills/`）

安装时**不**会带 `config.yml`、`docs/`（前者含真实凭据、后者是本地产物）；重装/升级时
目标目录里已有的 `config.yml`、`docs/` 会自动备份、装完原样移回，不丢失。

### 2. 跑 check_config.py 拿到机器可读状态

装完之后（或在已装好的 skill 目录上单独跑一次）调用 check_config.py，**默认输出就是 JSON**：

```bash
SK="<上一步装到的目录，如 ~/.trae-cn/skills/hive-ddl-to-mysql-skill>"
python3 "$SK/scripts/check_config.py" --skill-dir "$SK"           # 默认 JSON
# 也可显式:
python3 "$SK/scripts/check_config.py" --skill-dir "$SK" --json
```

脚本会**按安装路径自动识别 agent**（JSON 里的 `agent` 字段：`trae` / `claude` / `unknown`），
输出形如：

```json
{
  "ready": false,
  "agent": "trae",
  "skill_dir": "/Users/.../.trae-cn/skills/hive-ddl-to-mysql-skill",
  "config_path": null,
  "config_exists": false,
  "password_from_env": false,
  "missing": [
    {"field": "host", "reason": "config.yml 不存在"},
    {"field": "username", "reason": "config.yml 不存在"},
    {"field": "password", "reason": "config.yml 不存在且未设 MYSQL_PASSWORD 环境变量"}
  ],
  "warnings": [],
  "next_actions": [
    {"step": "copy_template", "cmd": "cp <SK>/config.example.yml <SK>/config.yml", "from": "...", "to": "..."},
    {"step": "ask_user", "fields": ["host","password","username"], "hint": "向用户询问 MySQL 连接信息；不要猜，不要用占位值。密码写入 config.yml 的 mysql.password（本地文件，写完 chmod 600，不入库）；trae-cn / claude 两个 agent 通用……"},
    {"step": "write_config", "path": "<SK>/config.yml", "note": "按 config.example.yml 的 mysql: 段结构填入……"},
    {"step": "chmod", "cmd": "chmod 600 <SK>/config.yml", "path": "...", "mode": "600"},
    {"step": "verify", "cmd": "python3 <SK>/scripts/check_config.py --skill-dir <SK>", "expect": "ready=true 即可 --execute……"}
  ]
}
```

退出码：`0` = 就绪；`2` = 缺 config.yml；`3` = 配置不完整或缺依赖。

- 用户只要转 DDL → **本步可整体跳过**，直接到第 6 步汇报安装完成。
- `ready=true` → **跳到第 6 步**，不用再做任何配置。
- `ready=false` 且用户要用 `--execute` → 按 `next_actions` 顺序往下走。

### 3. 按 next_actions 执行；缺什么就问用户要什么

- `step: install_dep` → `pip install pyyaml`（仅当 PyYAML 缺失时出现）。
- `step: copy_template` → 用 Shell 跑 `cmd` 里的 `cp` 命令把模板复制成 `config.yml`。
- `step: ask_user` → **必须**问用户要 `fields` 列出的字段，**不要猜、不要用占位值 127.0.0.1**。
  - `host`：真实 MySQL 地址（模板里的 `127.0.0.1` 是占位，原样保留会被判为未配置）。
  - `port`：端口，默认 3306（用户没特殊说就用这个）。
  - `username`：MySQL 账号。
  - `password`：MySQL 密码——**两个 agent 统一**写本地 `config.yml` 的 `mysql.password`
    （权限 600、不入库），不回显、不上传。`agent=claude` 时也可告知用户备选：
    配 `MYSQL_PASSWORD` 到 `~/.claude/settings.json` 的 `env` 段（改完重开会话）；
    `agent=trae` 时没有等价 settings 机制，直接写 config.yml。
  - `database`：目标库名，可空（告警不阻塞，用时 `--db` 覆盖）。
- `step: write_config` → 用 Write/Edit 工具把 `path` 指向的 `config.yml` 改好。
- `step: chmod` → 用 Shell 跑 `cmd` 锁 600。
- `step: verify` → 用 Shell 跑 `cmd` 再检测一次；ready=true 即配置完成，仍 false 把
  `missing` 贴回给用户继续补。

### 4. 写 config.yml（用 Write/Edit 工具，结构如下）

```yaml
mysql:
  host: "<用户给的真实 MySQL 地址>"
  port: 3306
  username: "<MySQL 账号>"
  password: "<MySQL 密码>"
  database: "<目标库名，可留空>"
  charset: "utf8mb4"
```

### 5. 锁权限 + 复跑 check_config 验证

```bash
chmod 600 "$SK/config.yml"
python3 "$SK/scripts/check_config.py" --skill-dir "$SK" --json
```

- `ready=true` → 配置完成。
- `ready=false` → 把新的 `missing` 项贴回给用户继续补齐，回到第 3 步。
- 两个 agent 各装了一份时，配置只引导其中一份（JSON 对应的那份）；另一份直接
  `cp` 同一份 `config.yml` 过去并 `chmod 600` 即可。

### 6. 汇报

向用户精简汇报：装到哪个目录（trae-cn / claude / 项目级，可能两份）、是否就绪、缺啥。
**不要**在对话里回显密码或完整凭据——只说"已写入 config.yml（权限 600）"。
用户只要转 DDL 时，明确告诉他"装完即可用，无需配置；以后要 --execute 再跑 check_config.py"。

### 7. 接下来

skill 就绪后，按下面的"前置：连接配置"和"怎么做"两节正常使用即可。

---

## 前置：什么时候需要连接配置

**只生成语句不需要账号密码**——这是默认路径，直接就能跑。
生成语句时只会去 `config.yml` 取一个 `mysql.database` 当目标库名（见下），取不到就生成裸表名。

加 `--execute` 真的建表时才需要完整连接信息，trae-cn / claude 两个 agent 适配如下：

- **主路径（两个 agent 通用）**：连接信息（含密码）写在本技能目录的 `config.yml` 的
  `mysql:` 段，写完 `chmod 600`、不入库。首次由上面的「Agent 一键安装与配置引导」
  问齐写好；`config.example.yml` 是模板。
- **密码的可选替代**：环境变量 `MYSQL_PASSWORD`。Claude Code 可配在
  `~/.claude/settings.json` 的 `env` 段（改完要重开会话才生效）；Trae-CN 没有等价的
  settings 机制，直接写 `config.yml` 即可。
- **其余非敏感项**（`host / port / username / database / charset`）：`config.yml` 的
  `mysql:` 段。查找顺序：`--config` 指定的路径 → 环境变量 `MYSQL_CONFIG` →
  **本技能目录**下的 `config.yml` → `~/.mysql/config.yml`。

CLI 参数（`--host/--user/--password/...`）和环境变量（`MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD/...`）
可覆盖任意字段，优先级 **CLI > 环境变量 > config.yml**。

脚本报「缺少连接参数」时，**不要**去猜 host / 账号密码，如实告诉用户，先跑
`python3 "$SK/scripts/check_config.py" --skill-dir "$SK"`（默认 JSON）看 `missing`，
按报错项分别提示：缺密码 → 检查本技能目录下 `config.yml` 的 `mysql.password`
（Claude 下也可检查 `MYSQL_PASSWORD` 环境变量，改完要重开会话），
缺其它字段 → 同样检查该 `config.yml`。

### 目标库名不沿用 Hive 库名

Hive 侧的库（`my_db`、`dw` 等）和 MySQL 侧的库是两套命名，把源库名照抄过来基本都是错的。
所以脚本**不用** Hive DDL 里解析出的库名，目标库固定走
**`--db` > 环境变量 `MYSQL_DATABASE` > `config.yml` 的 `mysql.database`**。
本仓库 `config.yml` 里配的是 `target_db`，因此默认产出的就是
`` `target_db`.`表名` ``，不是 `` `my_db`.`表名` ``。

源库名和目标库名不一致时会记一条告警，把这次「换库了」的事实摆在报告里。
用户明确要求落到别的库时用 `--db`；不想要库名前缀就 `--strip-db`。

## 怎么做

```bash
SK="<本技能目录>"

# ① 主路径：Hive DDL 落成文件再转（推荐，DDL 通常很长且带引号换行）
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file /tmp/hive_ddl.sql

# ② DDL 很短时直接传
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl "create table db.t (a string comment '甲')"

# ③ 用户明确说「建表 / 执行」时才加 --execute
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --execute --db mydb
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --execute --create-database --db newdb

# ④ 改表名/库名、加主键和索引
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql \
  --table t_new --db mydb --primary-key order_no --index user_id,dt --not-null user_id

# ⑤ 不想要字段名启发式，所有 string 统一给一个类型
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --string-type 'VARCHAR(512)'

# ⑥ 只有 describe 输出、没有建表语句时
python3 "$SK/scripts/hive_ddl_to_mysql.py" --format describe --ddl-file cols.txt --table t
```

**用户在对话里贴了很长的 Hive DDL**：先用 Write 把它写成 `.sql` 文件再 `--ddl-file` 跑，
比拼一条巨长的 `--ddl` 命令行可靠得多（DDL 里的单引号中文注释很容易把命令行搞坏）。

**用户在 IDE 里打开了建表语句文件**（上下文里有该文件路径）而没另外贴 DDL 时，
默认就把那个文件当输入，跑之前确认一句。

## 默认不建表

`CREATE TABLE` 是写操作。默认只生成语句、落盘、打到 stdout，**不连数据库**。

只有用户**明确要求**（「帮我建出来」「在 mysql 里执行」「同步过去」）时才加 `--execute`。
用户只说「把建表语句转一下 / 写出来 / 给我看看」时，不要自作主张去建表。
不确定就先出语句，然后问一句要不要执行。

`--execute` 时的动作顺序：连库 →（`--create-database` 时）建库 → `USE` → 建表 →
回查 `information_schema` 确认表存在和列数。默认带 `IF NOT EXISTS`，所以**重复跑不会报错，
但也不会改已存在表的结构**——表已存在时列数可能和你刚生成的 DDL 不一致，
脚本汇报的列数就是用来暴露这件事的，发现不一致要如实说出来。

## 脚本行为要点

- **类型映射**：`bigint/long→BIGINT`、`int→INT`、`double→DOUBLE`、`boolean→TINYINT(1)`、
  `timestamp/datetime→DATETIME`、`date→DATE`、`binary→BLOB`。
  `decimal(20,6)` **保留精度**原样带过去；`decimal` 不带精度时按 `--decimal-default`（默认 `20,6`）。
- **`string` 按字段名启发式给长度**（只看字段名的**最后一个** token，即 snake_case 的语义中心词）：
  - 中心词是 `remark/desc/content/json/ext/url/address/reason/title/…` → `TEXT`
  - 中心词是 `id/no/code/time/date/type/status/flag/…` → `VARCHAR(64)`
  - 中心词是 `name/channel/source/city/study/…` → `VARCHAR(128)`
  - 其余 → `VARCHAR(255)`（`--varchar-len` 可调）
  只看最后一个 token 是刻意的：`no_comment_col` 里的 `comment` 是修饰语不是中心词，
  按任意位置匹配会把它错判成 `TEXT`。`--string-type` 可整体关掉这档启发式。
- **复杂类型**：`array/map/struct/uniontype` → `JSON`（`--complex-type TEXT` 可改），并告警。
  尖括号内的逗号不会把列切错（`map<string,string>` 是一列，不是两列）。
- **分区字段降级成普通列**：Hive 的 `PARTITIONED BY (dt string)` 在 MySQL 没有对应概念，
  默认把 `dt` 作为**普通列**追加到列尾并保留注释，**不建索引**。
  要按天查得快就自己加 `--index dt`。`--partition-mode drop` 可整体丢弃分区字段。
- **保留字与标识符**：所有列名/表名**一律反引号包裹**，所以 `order`、`key`、`desc`
  这类保留字和中文列名都不用特判；命中保留字时额外告警一句，提醒下游查询也得带反引号。
- **重名列**：MySQL 不允许重名列，保留第一次定义、丢弃后续并告警。
- **注释**：原样保留并转义（先转反斜杠再转单引号），换行压成空格，超 1024 字节按字符边界截断。
  含逗号和括号的中文注释能正确穿过解析（这是解析器最容易错的地方，已覆盖）。
- **主键与索引默认都不加**。猜出来的主键会静默拒绝重复行导致丢数据，必须 `--primary-key` 显式给。
  指定为主键的列会**自动补 `NOT NULL`**（否则 MySQL 静默改成 NOT NULL，DDL 和实际表结构不一致）。
  `--index` 里的 `TEXT/BLOB` 列会自动加前缀长度 191（不加 MySQL 直接报错）。
- **其余列默认 `DEFAULT NULL`**。Hive 表通常没有非空约束，硬加 NOT NULL 会让同步作业写失败。
  要非空用 `--not-null`（可重复）。
- **认不出的类型原样大写透传**，并在报告里单列告警——留给 MySQL 报错，比脚本瞎猜一个类型安全。

## 交付什么

DDL 同时打到 stdout（立即可用）和落盘，产物在 `docs/hive-ddl-to-mysql/`：

- `<表名>_<时间戳>.sql` —— MySQL 建表语句，头部注释记录生成时间、列数、告警数、是否已执行。
- `<表名>_<时间戳>_字段核对报告.md` —— 字段映射明细表（字段/Hive 类型/MySQL 类型/注释/映射说明/来源）、
  **需人工确认的启发式类型**单列小节、全部告警、以及完整 DDL（使报告自包含）。
- `<表名>_<时间戳>_字段映射.csv` —— 同样的映射表，方便贴进表格评审。

跑完在对话里精简汇报：列数、**有多少列的类型是按字段名猜的**、分区字段怎么处理的、
告警条数、产物路径；执行了的话再报一句库名和实际列数。

**必须把启发式推断的 varchar 长度念给用户看**，尤其是给了 `VARCHAR(64)` 的那些——
这是唯一会**静默截断生产数据**的一档，不能和确定的映射混为一谈。

## 说明与边界

- **`string` → 长度是猜的，猜短了会截断数据**。字段名启发式只是省事，不是权威依据。
  真实数据可能远超推断长度（比如 `remark` 型内容进了 `_name` 字段）。拿不准就
  `--string-type TEXT` 或把长度调大——MySQL 里 `varchar` 只按实际内容占空间，给宽一点几乎不亏。
- **不做数据迁移**，只管表结构。搬数据是另一回事（导出可用 `hive-sql-to-csv`）。
- **表已存在时不改结构**。默认 `IF NOT EXISTS` 只保证不报错，不会 ALTER 对齐字段；
  需要改结构请人工写 `ALTER TABLE`，脚本不生成也不执行 DDL 变更。
- **`array/map/struct` → JSON 需要上游配合**：MySQL 的 JSON 列要求写入合法 JSON 文本，
  Hive 侧直接把复杂类型 toString 出来的格式通常**不是**合法 JSON。落地前确认写入端格式，
  或改用 `--complex-type TEXT`。
- 本地只校验结构，校验不了目标 MySQL 的版本差异（`JSON` 需 5.7+，`utf8mb4` 索引长度限制等）。
  首次使用建议先在测试库跑一次。
- 不查字段字典补中文注释（那是 `dataworks-sql-table` 的 `ddl` 子命令，产出的是 ODPS 建表语句）；
  不在 Hive 上执行 SQL（那是 `hive-sql-to-csv`）；不追字段血缘（那是 `sql-field-lineage`）。
