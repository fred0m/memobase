# merge_yolo 画像合并失效修复 — 完整记录

> 日期：2026-09-18
> 状态：✅ 已修复并上线，观察期（验收 D）
> 上游：issue #166 · PR #167

---

## 一、问题

memobase 的 `merge_yolo` 任务（画像层合并）大面积静默失败，日志大量：
```
WARNING - No Corresponding Merge Action: {'memo_id': N, ...}
```
后果：**画像层更新严重滞后**（事件层不受影响）。生产实测某些时期失败率 70~100%。

---

## 二、根因（两个独立问题）

### 模式②：解析器零容错 + prompt 自相矛盾

**触发链**：

1. prompt `zh_merge_profile_yolo.py` 的「## 输出模版」段写的是**字面占位符**：
   ```
   THOUGHT
   ---
   1. ACTION{tab}...
   2. ACTION{tab}...
   ```
   而同一份 prompt 的「## 输出动作」段示例却是正确的 `N. APPEND{tab}APPEND`。
   **三处描述互相矛盾**，模型大批次时跟了模板（照抄 `ACTION`）。

2. 模型输出 → `1. ACTION::APPEND`

3. 解析器 `prompts/utils.py:parse_string_into_merge_yolo_action`：
   ```python
   parse_line = clean_line.split("::")   # → ["ACTION", "APPEND"]
   action = parse_line[0].upper()        # → "ACTION"
   if action not in {"APPEND","UPDATE","ABORT"}:
       continue                          # ← 静默丢弃（真动作就在 parts[1] 里！）
   ```

**等价复现**（容器内实测）：
```
"1. ACTION::APPEND"          → {}   ❌
"1. APPEND"（裸动作词）        → {}   ❌
"1. ACTION::UPDATE::<全文>"   → {}   ❌
"1. UPDATE::memo"            → ✓
"2. APPEND::APPEND"          → ✓
```

### 模式①：空响应（旧 9router 后端时期）

- 173 次 `<raw_response>  </raw_response>`（LLM 返回空串）
- 9router 侧 75/82 次完成记录 **输出 token = 0**；92% 流量走 `ollama/deepseek-v4-flash:0731-cloud` 强制转换渠道
- **不是** SSE 解析失败、**不是**异常被吞、**基本不是**超时（48h 真超时仅 2 次）
- **换本地模型后端后 0 复发**（该渠道已不在链路）

### 附带发现

- `merge_yolo.py` 的 `llm_complete` **没传 max_tokens**，走默认 **1024** → 30+ 条 memo 各写完整 UPDATE 全文会**截断**
- `Failed to organize profiles: CODE 520` ×7（全在旧模型时期，P1 观察项）

---

## 三、修复（4 文件 / +29 / -12）

| # | 文件 | 改动 |
|---|---|---|
| P1 | `prompts/utils.py` | 解析器容错：剥 `ACTION::` 前缀；接受裸 `APPEND`/`ABORT`（裸 `UPDATE` 仍丢——无法复原 memo） |
| P2 | `prompts/zh_merge_profile_yolo.py`<br>`prompts/merge_profile_yolo.py` | 模板段改具体示例 + 明确"只能三选一"+ 删「直接输出单词」暗示 + 删示例行尾 `;` |
| P3 | `controllers/modal/chat/merge_yolo.py` | `max_tokens=4096` |
| P4 | 同上 | 守卫日志 `MERGE_YOLO_ZERO_ACTIONS`（解析零动作且输入非空时 ERROR） |

**为什么必须一起做**（图图给的反例）：
- 只做 P1 → 双段输出的 UPDATE memo 尾部带推理句污染会被救回
- 只做 P2 → 无兜底，模型再发挥一次就再丢一批

---

## 四、协作流程（图图出方案 → agy 实现 → 小爱验收）

1. **小爱调查**：按时期分流统计失败模式（发现我前后错了三次——先说 prompt 矛盾、再说截断、最后按时期分流才对）
2. **图图出方案**：`architect/designs/2026-09-18-memobase-merge-yolo-fix.md`（24.6KB）
   - 它**纠正了小爱两处统计错误**（"timeout ×137" 是假命中，真超时仅 2 次；"41 次 ACTION::" 任何口径都复现不出）
   - 挖出小爱漏掉的 `max_tokens` 默认 1024 问题
   - 明确硬前置：**api-src 不是 git 仓库，打补丁前必须 `git init`**
3. **agy 实现**：5 个 commit（第 5 个是小爱补的——**agy 漏改英文版一行**）
4. **小爱独立复验**：9/9 解析器断言 + 4 文件 py_compile

---

## 五、上线

### 目标分支（重要）

**不是 `main`** —— 生产的 `feature/summary-layer` 分支（含概要层 + 蒸馏等自家改动），`main` 上没有。

```
fix/merge-yolo-parser → 合并到 feature/summary-layer (caad534)
```

### 步骤

```bash
# 1. dockercenter 建 git 基线（硬前置）
ssh dockercenter 'cd /home/momo/memobase/api-src && git init && git add -A && git commit -m baseline'
# 2. 同步 4 文件 + 提交
# 3. 打回滚 tag
docker tag memobase-summary-layer:latest memobase-summary-layer:rollback-<ts>
# 4. 重建镜像
cd /home/momo/memobase/api-src && docker build -t memobase-summary-layer:latest .
# 5. 换容器（⚠️ --env-file 绝不能漏！）
docker run -d --name memobase-server --env-file /home/momo/memobase/memobase.env \
  -p 8019:8000 -v /home/momo/memobase/config.yaml:/app/config.yaml \
  --restart unless-stopped memobase-summary-layer:latest
```

### 验证结果

```
✅ LLM sanity check passed / Start Memobase Server 0.0.42
✅ 补丁在容器内（action_prefix ×3 / 守卫日志 / max_tokens / prompt 零残留）
✅ 验收 C 端到端：Adding 5 profiles，No Corresponding = 0（修复前必有）
✅ 测试用户已清理
```

---

## 六、验收 D（观察中）

**cron job**：`fb3a0aaf2150`（每 6h，no_agent，脚本 `~/.hermes/scripts/memobase_merge_watch.py`）

| 指标 | 目标 | 首轮实测 |
|---|---|---|
| `No Corresponding` /24h | 趋零（基线 38~172/天） | **0** ✅ |
| `MERGE_YOLO_ZERO_ACTIONS` | 0 | **0** ✅ |
| 画像日更新 | ≥21（基线） | **21** ✅ |
| 回归红线（其他任务报错） | 0 | **0** ✅ |

脚本行为：**正常时零输出（不打扰）**；异常或恢复时推送。已双向验证。

---

## 七、上游贡献

- **Issue #166**：https://github.com/memodb-io/memobase/issues/166
- **PR #167**：https://github.com/memodb-io/memobase/pull/167
  - 基于上游 main 的**干净分支** `pr/merge-yolo-fix`（cherry-pick 5 个 commit，只带 4 文件）
  - 上游 main 与我们的改动前版本**四个文件哈希完全一致** → 可干净 cherry-pick

**上游活跃度**（核实）：代码 push 停在 **2026-01-11**，32 个 open issue 未处理 → 确认不活跃。

---

## 八、后续维护定位：fork-first

**结论：memobase 后续主要自己维护 fork 版本**（上游 8 个月没动代码）。

### 维护体系

| 位置 | 作用 |
|---|---|
| `~/hotProject/Hermes/memobase`（git） | 主仓库（remote: forgejo + origin + fork） |
| 分支 `feature/summary-layer` | **生产分支**（概要层 + 蒸馏 + merge_yolo 修复） |
| 分支 `pr/merge-yolo-fix` | 上游 PR 用（基于 origin/main，干净） |
| dockercenter `/home/momo/memobase/api-src` | 构建源（**已建 git 基线**，commit f7bfed3 是改动前） |
| 镜像回滚点 | `memobase-summary-layer:rollback-20260918-145739` |

### 升级上游时的注意事项

1. **重新构建镜像 = 拉上游源码覆盖 api-src** → 会冲掉我们的补丁
2. 防线：api-src 的 git 基线 + 补丁 commit 序列（`8c9f0c8`），升级时 cherry-pick 或 re-export
3. 若上游收了 PR #167 → 该补丁可从维护清单移除

### 补丁清单（当前挂在我们 fork 上的改动）

| 改动 | commit | 上游状态 |
|---|---|---|
| 概要层（user_summaries + 4 路由 + flush 钩子） | 26db42f | 未提（自家需求） |
| UserSummary dataclass 字段顺序修复 | 90abe68 | 未提 |
| 概要蒸馏改 llm style | 604e663 | 未提 |
| **merge_yolo 修复（本记录）** | 5 commits | **已提 PR #167** |

---

## 九、本次踩过的坑（已沉淀 skill）

1. **`UID` 是 bash 内置只读变量** —— `UID=$(...)` 赋值静默失败，拿到当前用户 UID(1000)，导致 API 连续报 uuid 格式错。用 `TUID` 等自定义名。
2. **拷源码进容器验证时，建目录/拷贝会假失败** —— 断言全红先怀疑拷贝链路，再怀疑 agent。必带 `--exclude='__pycache__'`，拷完先 `grep -c` 自检。
3. **agent 漏改多语言副本** —— 任务书写"zh+en 同步"，agy 只改了 zh。验收要逐份 grep。
4. **目标分支差点选错** —— 默认以为合 main，实际生产在 feature/summary-layer。
5. **验收脚本要先验证有效性** —— 拿未改动源码跑一遍，断言挂掉才能证明测试能区分好坏。

---

## 十、文件清单

| 文件 | 说明 |
|---|---|
| `architect/designs/2026-09-18-memobase-merge-yolo-fix.md` | 图图的方案（含精确到行的补丁 spec） |
| `memobase/upstream-issue-draft.md` | 上游 issue 草稿（已发为 #166） |
| `~/.hermes/scripts/memobase_merge_watch.py` | 验收 D 巡检脚本 |
| `~/.hermes/backups/memobase-merge-fix-20260918-144444/` | 改动前备份（worktree 打包 + 4 个 .orig） |
