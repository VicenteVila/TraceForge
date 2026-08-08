import random

import traceforge

traceforge.configure(collector="sqlite", db_path="cogniteam_traces.db")

class ScopingAgent:
    @traceforge.trace(agent="scoping", model="gemini-2.5-flash", tags=["classification"])
    def classify(self, description: str) -> dict:
        if "error" in description.lower():
            raise ValueError("Cannot classify ambiguous input")
        tf = traceforge
        with tf.span(agent="scoping_llm", model="gemini-2.5-flash", tags=["llm_call"]) as sp:
            sp.set_tokens(input=350, output=120)
        return {"domain": "web_development", "archetype": "landing-page"}

class PlannerAgent:
    @traceforge.trace(agent="planner", model="llama-3.3-70b")
    def generate(self, manifest: dict) -> list:
        tf = traceforge
        with tf.span(agent="planner_llm", model="llama-3.3-70b", tags=["llm_call"]) as sp:
            sp.set_tokens(input=800, output=600)
        return [
            {"step": 1, "action": "create_html"},
            {"step": 2, "action": "add_css"},
            {"step": 3, "action": "add_js"},
        ]

class DeveloperAgent:
    @traceforge.trace(agent="developer", model="deepseek-coder:6.7b")
    def execute(self, plan: list) -> dict:
        tf = traceforge
        with tf.span(agent="developer_llm", model="deepseek-coder:6.7b", tags=["llm_call"]) as sp:
            sp.set_tokens(input=1200, output=900)
        if random.random() < 0.3:
            raise RuntimeError("Failed to execute step 2")
        return {"files": ["index.html", "styles.css", "app.js"]}

class DebuggerAgent:
    @traceforge.trace(agent="debugger", model="gpt-4o-mini")
    def diagnose(self, error: Exception, plan: list) -> list:
        tf = traceforge
        with tf.span(agent="debugger_llm", model="gpt-4o-mini", tags=["llm_call"]) as sp:
            sp.set_tokens(input=500, output=200)
        return [{"step": 2, "action": "fix_css_import"}]

class Orchestrator:
    def __init__(self):
        self.scoping = ScopingAgent()
        self.planner = PlannerAgent()
        self.developer = DeveloperAgent()
        self.debugger = DebuggerAgent()

    @traceforge.trace(agent="orchestrator", model=None)
    def run(self, task: str) -> dict:
        manifest = self.scoping.classify(task)
        plan = self.planner.generate(manifest)

        try:
            artifacts = self.developer.execute(plan)
        except Exception as e:
            fix = self.debugger.diagnose(e, plan)
            print("  Debugger generated fix, re-executing...")
            artifacts = self.developer.execute(fix)

        return artifacts


if __name__ == "__main__":
    orch = Orchestrator()

    for i in range(3):
        print(f"\n=== Run {i+1} ===")
        try:
            result = orch.run(f"Build landing page for client {i+1}")
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Pipeline failed: {e}")

        trace_id = traceforge.get_last_trace_id()
        print(f"\nTrace tree for run {i+1}:")
        traceforge.show(trace_id)

print("\n\n=== STATS ===")
traceforge.show(traceforge.list_traces(limit=1)[0])
