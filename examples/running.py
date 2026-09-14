import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print("child", child.pid, flush=True)
while True:
    time.sleep(0.1)
