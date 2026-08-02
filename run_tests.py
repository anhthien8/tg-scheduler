import sys
import os
import pytest
import shutil

def main():
    print("=" * 60)
    print("Starting E2E Test Suite for tg-scheduler...")
    print("=" * 60)
    
    # Run pytest programmatically
    args = [
        "-v",
        "--tb=short",
        os.path.join(os.path.dirname(__file__), "tests")
    ]
    
    exit_code = pytest.main(args)
    
    # Cleanup any leftovers
    print("=" * 60)
    print(f"Test suite finished with exit code: {exit_code}")
    print("=" * 60)
    
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
