"""Hold the development lock for one automation turn. Enter releases it."""
import bootstrap
from rainforge_recovery import ProjectLock

with ProjectLock(bootstrap.ROOT / "automation" / "development.lock") as lock:
    print(f"LOCKED pid={__import__('os').getpid()} recovered={bool(lock.recovered)}", flush=True)
    input()
print("RELEASED", flush=True)
