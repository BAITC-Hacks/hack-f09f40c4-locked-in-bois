"""Exercise the judge's actual command, including Windows stdout encoding."""

import os
from pathlib import Path
import subprocess
import sys


def test_judge_check_fast_mode():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "cp1251", "LLM_PROVIDER": "openai",
           "OPENAI_API_KEY": "judge-check-dummy-key", "NVIDIA_API_KEY": "judge-check-dummy-key"}
    result = subprocess.run(
        [sys.executable, "scripts/judge_check.py"], cwd=root, env=env,
        capture_output=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    for number in ("62.99", "57.06", "54.65", "56.63", "49.18", "52.56",
                   "56.54", "95", "55.67", "57.24", "694,395"):
        assert number in result.stdout
    for criterion in range(1, 6):
        assert f"PASS | Критерий {criterion}" in result.stdout
    assert "PASS | Защита чисел" in result.stdout
    assert "PASS | Время отклика" in result.stdout
    assert "PASS | Итог проверки" in result.stdout
    assert "Полный перебор: ожидайте" not in result.stdout
