# Community Detection + Process Detection 性能优化设计

## 优化 1：Community Detection — CSV batch write

### 当前瓶颈

```python
# 问题 1: Python dict 加载限制 10000 条边
for rel_type in ("Calls", "Contains", "Inherits", "ConformsTo"):
    rows = conn.execute(
        f"MATCH ... LIMIT 10000"     # ← 截断大模块的边
    ).get_all()
    for row in rows:
        adj[row[0]].add(row[1])      # ← Python dict 逐条插入

# 问题 2: 逐条 MERGE 写入
for member_usr in members:
    conn.execute(
        "MATCH (s:Symbol {usr: $usr}), (c:Community {id: $cid}) "
        "MERGE (s)-[:MEMBER_OF]->(c)",   # ← 每行一次 Ladybug 调用
        {"usr": member_usr, "cid": community_id},
    )
```

### 优化方案

边加载：去掉 `LIMIT`，全量 Cypher 查询 → `adj` dict。一次查询 O(edges)，和之前的一样快。

写入：模拟 `upsert_symbols` 的 CSV batch 模式。

```python
def run_community_detection(conn, target_id, batch_size=10000):
    # Step 1: 全量加载边（一次性 Cypher）
    adj: dict[str, set[str]] = defaultdict(set)
    for rel_type in ("Calls", "Contains", "Inherits", "ConformsTo"):
        rows = conn.execute(
            f"MATCH (a:Symbol)-[:{rel_type}]->(b:Symbol) "
            f"RETURN a.usr, b.usr"
        ).get_all()
        for row in rows:
            adj[row[0]].add(row[1])
            adj[row[1]].add(row[0])

    # Step 2: Label propagation（保持不变，这是算法本身）
    ...

    # Step 3: CSV batch 写入 Community 节点
    import csv, tempfile, os
    csv_path = os.path.join(tempfile.mkdtemp(), "communities.csv")
    with open(csv_path, "w") as fh:
        w = csv.writer(fh)
        for lbl, members in groups.items():
            if len(members) < 3:
                continue
            community_id = f"community:{target_id}:{lbl}"
            w.writerow([community_id, len(members)])
    conn.execute(f"COPY Community FROM '{csv_path}' (HEADER=false)")

    # Step 4: CSV batch 写入 MEMBER_OF 边
    rel_csv = os.path.join(tempfile.mkdtemp(), "member_of.csv")
    with open(rel_csv, "w") as fh:
        w = csv.writer(fh)
        for lbl, members in groups.items():
            if len(members) < 3:
                continue
            community_id = f"community:{target_id}:{lbl}"
            for usr in members:
                w.writerow([usr, community_id])
    conn.execute(f"COPY MEMBER_OF FROM '{rel_csv}' (HEADER=false)")
```

预计时间：Label propagation 本身 ~15s（取决于边数），CSV write ~2s。总计 ~20s。

### 副作用

无。算法逻辑不变，只改写入方式。CSV batch 写入和 `upsert_symbols` 一样的模式，已在 ingest 中验证过。

---

## 优化 2：Process Detection — 多源 BFS

### 当前瓶颈

```python
# 30 个入口点，每个独立 BFS，重复遍历中间节点
for entry in entries[:30]:          # 30 次循环
    callees = g.callees_of_depth(    # 每次 ~7s
        entry["usr"], depth=5
    )
```

`didFinishLaunching` 的 BFS 访问了 Application、AppDelegate、ServiceManager 等中心节点。下一个入口点 `imCmdInvitationNotification` 又要重新访问相同的 NotificationManager、MeetingService 等节点。共享节点被重复查询。

### 优化方案

单次多源 BFS：所有入口点作为 seed frontier，一次遍历收集所有节点的归属。

```python
def callees_of_depth_batch(conn, entry_usrs: list[str], depth: int = 5):
    """Multi-source BFS: return {entry_usr: [callee_dict, ...]}."""
    g = GraphLookup(conn)

    # Seed: all entries start at depth 0
    # frontier 是 {usr: set of source_entry_usrs}
    seen: dict[str, set[str]] = {u: {u} for u in entry_usrs}
    frontier: set[str] = set(entry_usrs)
    results: dict[str, list[dict]] = {u: [] for u in entry_usrs}

    for d in range(1, depth + 1):
        next_frontier: set[str] = set()
        # 批量查询所有 frontier 节点的 callees
        for f_usr in frontier:
            for callee in g.callees_of(f_usr):
                if callee["usr"] not in seen:
                    seen[callee["usr"]] = seen.get(f_usr, set())
                    next_frontier.add(callee["usr"])
                else:
                    # 节点已被其他入口访问过，标记共享
                    seen[callee["usr"]].update(seen.get(f_usr, set()))

                # 记录归属：这个 callee 被哪些入口点发现
                for src in seen.get(f_usr, {f_usr}):
                    callee_copy = {**callee, "depth": d}
                    if callee_copy not in results[src]:
                        results[src].append(callee_copy)

        if not next_frontier:
            break
        frontier = next_frontier

    return results
```

process detection 调用改为：

```python
all_callees = callees_of_depth_batch(
    conn, [e["usr"] for e in entries[:30]], depth=5)

for entry in entries[:30]:
    callees = all_callees[entry["usr"]]
    if len(callees) < min_steps:
        continue
    ...
```

预计时间：30 次独立 BFS (218s) → 1 次共享 BFS (~15s)。因为共享节点只查一次。

### 副作用

每个入口点的 callee 结果可能比独立 BFS 略多（因为通过共享节点的间接路径）。但不影响 process 质量 — process 关心的是一组入口点能到达的所有节点。

---

## 实施计划

1. 改 `community_detection.py`：去 LIMIT + CSV batch write
2. 改 `lookup.py`：加 `callees_of_depth_batch()`
3. 改 `process_detection.py`：用 batch BFS 替代独立 BFS
4. 测试验证
5. 在 Zoom 图上跑：预期 ingest + community + process < 150s
