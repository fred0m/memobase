# memobase 仓库维护约定（fork-first）

> 建立：2026-09-18
> 起因：上游代码 push 停在 2026-01-11（8 个月未动），32 个 open issue 堆积 → **memobase 主要我们自己维护 fork**。
> 问题：三方（本地 / GitHub fork / 家里 forgejo）+ 多个分支，容易搞不清哪个是最新工程。

---

## 一、唯一真相源

**`feature/summary-layer` 分支 = 生产代码 = 唯一真相源。**

不要用 `main` 判断最新工程——历史上 `main` 落后过生产分支 16 个 commit，正是「搞不清哪个最新」的根源。

### 当前统一状态（2026-09-18）

| 位置 | commit | 说明 |
|---|---|---|
| 本地 `feature/summary-layer` | `c511b4d` | 开发主分支 |
| 本地 `main` | `c511b4d` | **已与生产分支同步**（保持同点） |
| GitHub fork `main` | `c511b4d` | ✅ 一致 |
| GitHub fork `feature/summary-layer` | `c511b4d` | ✅ 一致 |
| 家里 forgejo `feature/summary-layer` | `c511b4d` | ✅ 一致 |
| 家里 forgejo `main` | `12dbefc` | ⚠️ **受保护分支，推不上去**（见下） |

### ⚠️ forgejo 的 main 受保护

`protected_branch` 表：`repo_id=3 (memobase), branch_name=main, can_push=0, enable_whitelist=0, apply_to_admins=0`

这是**家里 git 的全局策略**（对所有仓库的 main 都设了），不是 memobase 特有。
→ **不要试图改它**；用 `feature/summary-layer` 作为 forgejo 上的代码载体。

---

## 二、远端配置

```bash
cd ~/hotProject/Hermes/memobase
git remote -v
#   forgejo  ssh://git@git.momo:2222/anon/memobase.git      ← 家里备份（主）
#   origin   https://github.com/memodb-io/memobase.git      ← 上游（只 fetch）
#   fork     https://github.com/fred0m/memobase.git         ← 我们的 GitHub fork
```

⚠️ **GitHub 走 HTTPS 不走 SSH**：SSH 到 github.com 会撞代理 fake-ip（198.18.0.249）连不通。
```bash
gh auth setup-git   # 让 git 用 gh 的凭据，一次性
```

---

## 三、日常操作

### 开发 → 上线 → 同步（标准循环）

```bash
cd ~/hotProject/Hermes/memobase

# 1. 在 feature/summary-layer 上开发（或建 worktree 交给 agent）
# 2. 上线到 dockercenter（见下节）
# 3. 推送备份
git push forgejo feature/summary-layer
git push fork feature/summary-layer

# 4. 让 main 跟生产分支保持同点（避免又分叉）
git checkout main && git merge --ff-only feature/summary-layer
git push fork main
#   ⚠️ forgejo 的 main 受保护，推不了（正常）
git checkout feature/summary-layer
```

### 上线到 dockercenter

```bash
# api-src 已建 git 基线（f7bfed3=初始，后续补丁各自 commit）
ssh dockercenter 'cd /home/momo/memobase/api-src && git log --oneline | head'

# 同步改动文件（只同步要改的，别整目录覆盖）
scp src/server/api/<file> dockercenter:/home/momo/memobase/api-src/<file>

# 提交 + 打回滚点 + 重建 + 换容器
ssh dockercenter 'cd /home/momo/memobase/api-src && git add -A && git commit -m "..."'
ssh dockercenter 'docker tag memobase-summary-layer:latest memobase-summary-layer:rollback-<ts>'
ssh dockercenter 'cd /home/momo/memobase/api-src && docker build -t memobase-summary-layer:latest .'
ssh dockercenter 'docker stop memobase-server && docker rm memobase-server && \
  docker run -d --name memobase-server --env-file /home/momo/memobase/memobase.env \
  -p 8019:8000 -v /home/momo/memobase/config.yaml:/app/config.yaml \
  --restart unless-stopped memobase-summary-layer:latest'
```

⚠️ **`--env-file` 绝不能漏**（DATABASE_URL 缺失 → 启动即崩）
⚠️ **`docker restart` 不换代码**——改代码必须重建镜像 + 换容器

### 给上游提 PR（干净分支）

```bash
git fetch origin main
git checkout -b pr/<name> origin/main
# 先验证：上游那几个文件的哈希 == 我们改动前版本
git cherry-pick <commits...>
git push -u fork pr/<name>
gh pr create --repo memodb-io/memobase --head fred0m:pr/<name> --base main
```

---

## 四、当前挂在 fork 上的补丁（升级上游时需保护）

| 改动 | 上游状态 | 恢复方式 |
|---|---|---|
| 概要层（user_summaries 表 + 4 路由 + flush 钩子） | 未提（自家需求） | 生产分支已有，重新 cherry-pick |
| UserSummary dataclass 字段顺序修复 | 未提 | 同上 |
| 概要蒸馏改 llm style | 未提 | 同上 |
| **merge_yolo 修复**（解析器+prompt+max_tokens+守卫） | **已提 PR #167 / issue #166** | 若被收 → 从清单移除 |

**升级上游的防线**：
1. 重建镜像 = 拉上游源码覆盖 api-src → **会冲掉所有补丁**
2. api-src 的 git 基线 + commit 序列是唯一防线
3. 升级前先 `git log` 记录当前补丁 commit，升级后逐个 cherry-pick 或 re-export

---

## 五、分支清单

| 分支 | 用途 | 远端 |
|---|---|---|
| `feature/summary-layer` | **生产分支（真相源）** | forgejo ✅ / fork ✅ |
| `main` | 与生产分支同步（历史兼容） | fork ✅ / forgejo ⚠️受保护 |
| `pr/merge-yolo-fix` | 上游 PR #167 用（基于 origin/main，只 4 文件） | fork ✅ |

已清理：`fix/merge-yolo-parser`（内容已合入生产分支，worktree 已删）

---

## 六、相关文档

- 本次修复完整记录：`notes/2026-09-18-merge-yolo-fix.md`
- 上游 issue 草稿：`notes/upstream-issue-166-draft.md`
- 架构方案（图图）：`~/hotProject/Hermes/architect/designs/2026-09-18-memobase-merge-yolo-fix.md`
- skill：`memobase-config`（含维护定位 + merge_yolo Pitfall）
