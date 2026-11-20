from dataclasses import dataclass, field, asdict
from enum import Enum


class Status(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    RETRYING = "RETRYING"
    RECOVERING = "RECOVERING"
    CANCELLED = "CANCELLED"


@dataclass
class Task:
    id: str
    goal: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    tools: list[str] = field(default_factory=list)
    agent: str = "file"
    model: str | None = None
    constraints: dict = field(default_factory=dict)
    expected_result: str = ""
    verification: str = ""
    status: str = Status.PENDING
    history: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 2


class TaskGraph:
    def __init__(self, tasks: list[Task]):
        self.tasks = {task.id: task for task in tasks}
        if len(self.tasks) != len(tasks):
            raise ValueError("Duplicate task ID")
        visiting, visited = set(), set()

        def visit(key):
            if key not in self.tasks:
                raise ValueError(f"Missing dependency: {key}")
            if key in visiting:
                raise ValueError("Dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            for dep in self.tasks[key].dependencies:
                visit(dep)
            visiting.remove(key)
            visited.add(key)

        for key in self.tasks:
            visit(key)

    def ready(self):
        result = []
        for task in self.tasks.values():
            if task.status in {Status.PENDING, Status.READY, Status.RETRYING, Status.RECOVERING} and all(
                self.tasks[dep].status == Status.COMPLETED for dep in task.dependencies
            ):
                if task.attempts < task.max_attempts:
                    task.status = Status.READY
                    result.append(task)
                else:
                    task.status = Status.FAILED
        return sorted(result, key=lambda item: (-item.priority, item.id))

    def serialize(self):
        return [asdict(task) for task in self.tasks.values()]
