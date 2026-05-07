#!/usr/bin/env python3
"""
main.py

Orchestrates the complete trending data pipeline:
1. Fetch trending data from multiple sources (GitHub, HackerNews, Dev.to, ProductHunt)
   and store in PostgreSQL
2. Embed the data using Google's Gemini API
3. Store embeddings in Pinecone for RAG

Run:
   python main.py
"""

import subprocess
import sys
import time
from datetime import datetime

def print_section(title):
    """Print a formatted section header"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def run_step(step_name, script_name):
    """Run a Python script as a subprocess"""
    print_section(f"STEP {step_name}: {script_name}")
    
    try:
        print(f"⏱️  Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        result = subprocess.run(
            [sys.executable, script_name],
            check=True,
            cwd="."
        )
        print(f"✅ Completed successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed with exit code {e.returncode}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    """Main pipeline orchestrator"""
    print(f"\n🚀 Starting Trending Data Pipeline")
    print(f"   Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    steps = [
        ("1", "Fetch Trending Data", "index.py"),
        ("2", "Embed & Store in Pinecone", "embed.py"),
    ]
    
    results = []
    
    for step_num, step_name, script in steps:
        success = run_step(step_num, script)
        results.append((step_name, success))
        
        if not success:
            print(f"\n⚠️  Pipeline halted due to failure in {step_name}")
            break
        
        # Wait a bit between steps
        time.sleep(2)
    
    # Print summary
    print_section("PIPELINE SUMMARY")
    
    for step_name, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"  {status} - {step_name}")
    
    all_success = all(success for _, success in results)
    
    if all_success:
        print(f"\n🎉 All steps completed successfully!")
        print(f"   End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return 0
    else:
        print(f"\n❌ Pipeline encountered errors")
        return 1

if __name__ == "__main__":
    sys.exit(main())
