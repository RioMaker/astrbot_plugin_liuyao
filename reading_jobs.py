"""Bounded, short-lived execution slots; never store a group's model history."""

import asyncio
from dataclasses import dataclass, field


@dataclass
class GroupJobs:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    count: int = 0


class ReadingJobs:
    def __init__(self, concurrency=2, waiting=2, wait_timeout=30):
        self.slots = asyncio.Semaphore(concurrency)
        self.waiting = waiting
        self.wait_timeout = wait_timeout
        self.groups = {}
        self.tasks = set()
        self.closed = False

    async def run(self, group, operation):
        if self.closed:
            return "六爻服务正在卸载，请稍后重试。"
        state = self.groups.get(group)
        if (state and state.count >= 1 + self.waiting) or sum(
            item.count for item in self.groups.values()
        ) >= 32:
            return "六爻解读繁忙，等待队列已满；本次未起卦、未扣次数，请稍后重试。"
        if state is None:
            state = self.groups[group] = GroupJobs()
        state.count += 1
        task = asyncio.current_task()
        self.tasks.add(task)
        group_acquired = global_acquired = False

        async def acquire():
            nonlocal group_acquired, global_acquired
            await state.lock.acquire()
            group_acquired = True
            await self.slots.acquire()
            global_acquired = True

        try:
            try:
                await asyncio.wait_for(acquire(), timeout=self.wait_timeout)
            except asyncio.TimeoutError:
                return "六爻解读等待超时；本次未起卦、未扣次数，请稍后重试。"
            return await operation()
        finally:
            if global_acquired:
                self.slots.release()
            if group_acquired:
                state.lock.release()
            state.count -= 1
            if not state.count:
                self.groups.pop(group, None)
            self.tasks.discard(task)

    async def close(self):
        self.closed = True
        tasks = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
