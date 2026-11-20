import bootstrap
import sys
from pathlib import Path
if len(sys.argv)==4 and sys.argv[1]=='--project-tests':
    from rainforge_worker.checks import runner
    raise SystemExit(runner(Path(sys.argv[2]),Path(sys.argv[3])))
if len(sys.argv)>=3 and sys.argv[1]=='--developer-python':
    from rainforge_worker.developer_terminal import run_python_script
    run_python_script(Path(sys.argv[2]), sys.argv[3:])
    raise SystemExit(0)
from rainforge_worker.__main__ import main
main()
