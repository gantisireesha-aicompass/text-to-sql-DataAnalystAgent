import unittest

from sql_agent.agent import SQLAgent
from sql_agent.config import Settings
from sql_agent.db import Database
from sql_agent.tools import ToolExecutor
from tests.fakes import FakeClient, make_db, response, text, tool_use

GOOD_SQL = "SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.Name"


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_db())

    def tearDown(self):
        self.db.close()

    def agent(self, script, **settings):
        client = FakeClient(script)
        s = Settings(model="claude-sonnet-5-5", **settings)
        return SQLAgent(client, ToolExecutor(self.db), s), client

    def test_happy_path_with_self_correction(self):
        agent, client = self.agent(
            [
                response(tool_use("list_tables")),
                response(tool_use("run_sql", {"sql": "SELECT Genre FROM Track"})),  # bad column
                response(tool_use("run_sql", {"sql": GOOD_SQL})),
                response(
                    text("Done."), tool_use("final_answer", {"answer": "Rock 2, Jazz 1", "sql": GOOD_SQL})
                ),
            ]
        )
        res = agent.run("Tracks per genre?")

        self.assertEqual(res.status, "answered")
        self.assertEqual(sorted(res.rows), [("Jazz", 1), ("Rock", 2)])
        self.assertEqual([s.tool for s in res.steps], ["list_tables", "run_sql", "run_sql"])
        self.assertTrue(res.steps[1].is_error)
        # The SQL error was sent back to the model as an is_error tool_result.
        error_turn = client.requests[2]["messages"][-1]["content"][0]
        self.assertTrue(error_turn["is_error"])
        self.assertIn("no such column", error_turn["content"])
        self.assertEqual((res.llm_calls, res.input_tokens, res.output_tokens), (4, 400, 80))
        self.assertAlmostEqual(res.cost_usd, (400 * 2 + 80 * 10) / 1e6)

    def test_failing_final_sql_is_returned_to_model(self):
        agent, client = self.agent(
            [
                response(tool_use("final_answer", {"answer": "x", "sql": "SELECT nope FROM Track"})),
                response(
                    tool_use("final_answer", {"answer": "3 tracks", "sql": "SELECT COUNT(*) FROM Track"})
                ),
            ]
        )
        res = agent.run("How many tracks?")
        self.assertEqual(res.status, "answered")
        self.assertEqual(res.rows, [(3,)])
        self.assertIn("final SQL failed", client.last_user_content()[0]["content"])

    def test_write_attempt_is_blocked_and_db_unchanged(self):
        agent, _ = self.agent(
            [
                response(tool_use("run_sql", {"sql": "DELETE FROM Track"})),
                response(tool_use("final_answer", {"answer": "I can't delete data.", "sql": ""})),
            ]
        )
        res = agent.run("Delete all tracks")
        self.assertTrue(res.steps[0].is_error)
        self.assertIn("guardrail", res.steps[0].output)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM Track").fetchone()[0], 3)

    def test_clarification_then_follow_up(self):
        clarify = tool_use("ask_clarification", {"question": "Which year?"})
        agent, client = self.agent(
            [
                response(clarify),
                response(tool_use("final_answer", {"answer": "3", "sql": "SELECT COUNT(*) FROM Track"})),
            ]
        )
        first = agent.run("How many tracks were added recently?")
        self.assertEqual(first.status, "needs_clarification")
        self.assertEqual(first.clarification, "Which year?")

        second = agent.run("All time is fine", previous=first)
        self.assertEqual(second.status, "answered")
        # The pending ask_clarification tool_use is answered with the user's reply.
        follow_up = client.requests[1]["messages"][-1]["content"]
        self.assertEqual(follow_up[0]["type"], "tool_result")
        self.assertEqual(follow_up[0]["tool_use_id"], clarify.id)
        self.assertIn("All time is fine", follow_up[0]["content"])

    def test_prose_reply_gets_one_nudge(self):
        agent, client = self.agent(
            [
                response(text("There are 3 tracks.")),
                response(tool_use("final_answer", {"answer": "3", "sql": "SELECT COUNT(*) FROM Track"})),
            ]
        )
        res = agent.run("How many tracks?")
        self.assertEqual(res.status, "answered")
        self.assertIn("final_answer", client.requests[1]["messages"][-1]["content"])

    def test_max_steps_budget(self):
        agent, _ = self.agent([response(tool_use("list_tables")) for _ in range(3)], max_steps=3)
        self.assertEqual(agent.run("loop forever").status, "max_steps")

    def test_too_many_errors(self):
        bad = [response(tool_use("run_sql", {"sql": "SELECT x FROM nope"})) for _ in range(4)]
        agent, _ = self.agent(bad, max_consecutive_errors=4)
        self.assertEqual(agent.run("?").status, "too_many_errors")


if __name__ == "__main__":
    unittest.main()
