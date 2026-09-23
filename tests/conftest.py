def pytest_configure(config):
    config.addinivalue_line("markers", "slow: exhaustive optimizer enumeration (RUN_SLOW=1)")
