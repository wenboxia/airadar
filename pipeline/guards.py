"""兜底、预算与并发：pipeline 的"安全带"。

设计目标（面试叙事：含兜底逻辑的完整 Agent 工作流）：
- 预算硬上限：单次运行 token/调用次数封顶，超限后 LLM 环节自动转降级路径，而不是中断运行
- 所有降级都要留痕（item.notes / stats），可审计
- 并发：LLM 调用是纯 I/O 等待，串行跑 6 条要 8 分钟。线程池并发后共享状态必须加锁
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor


class BudgetExceeded(Exception):
    pass


class Budget:
    """线程安全的预算账本（并发调用下 += 不是原子操作，必须加锁）。"""

    def __init__(self, token_limit: int, call_limit: int):
        self.token_limit = token_limit
        self.call_limit = call_limit
        self.tokens_used = 0
        self.calls_made = 0
        self.lock = threading.Lock()

    def can_spend(self) -> bool:
        with self.lock:
            return self.tokens_used < self.token_limit and self.calls_made < self.call_limit

    def record(self, tokens: int):
        with self.lock:
            self.calls_made += 1
            self.tokens_used += tokens

    def snapshot(self) -> dict:
        with self.lock:
            return {"tokens_used": self.tokens_used, "calls_made": self.calls_made,
                    "token_limit": self.token_limit, "call_limit": self.call_limit}


def retry(fn, attempts=3, base_delay=1.5, retriable=(Exception,)):
    """指数退避重试。最后一次仍失败则抛出，由调用方决定降级。"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retriable as e:  # noqa: PERF203
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last


def parallel_map(fn, items: list, workers: int = 6, on_error=None):
    """并发执行且单条失败不影响其他条目（舱壁原则的并发版）。

    保持输入顺序返回结果；出错的位置放 None 并回调 on_error(item, exc)。
    """
    if not items:
        return []
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = {pool.submit(fn, it): i for i, it in enumerate(items)}
        for fut, idx in futures.items():
            try:
                results[idx] = fut.result()
            except Exception as e:  # noqa: BLE001 单条失败被隔离
                if on_error:
                    on_error(items[idx], e)
    return results
