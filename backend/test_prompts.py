"""Tests for the escalation ladder, whitespace preservation, and the
rule-based bug detectors.

Stdlib unittest so there is nothing to install:

    cd backend
    python -m unittest -v

No API key and no network needed — the Groq call is stubbed, so these are safe
to run in CI or on a machine with no .env.
"""

import textwrap
import unittest

import app as tutor_app
from detectors import (
    TAG_ASSIGN_IN_CONDITION,
    TAG_INDENTATION,
    TAG_INPUT_NO_CAST,
    TAG_MISSING_RETURN,
    TAG_OFF_BY_ONE_INDEX,
    TAG_OFF_BY_ONE_RANGE,
    TAG_SCOPE,
    TAG_TABS_AND_SPACES,
    detect_bugs,
)
from prompts import (
    BASE_RULES,
    LEVEL_INSTRUCTIONS,
    build_system_prompt,
    build_user_message,
    level_for_attempt,
    trim_blank_lines,
)

# A method pasted out of a class: line 1 starts at 4 spaces, and that indent is
# exactly what a .strip() destroys.
INDENTED_FRAGMENT = (
    "    def check(self, mark):\n"
    "        if mark > 50:\n"
    "            return True\n"
    "        return False"
)


def indent_of(line):
    return len(line) - len(line.lstrip(" \t"))


class TrimBlankLines(unittest.TestCase):
    def test_preserves_leading_indent_of_first_line(self):
        self.assertEqual(trim_blank_lines(INDENTED_FRAGMENT), INDENTED_FRAGMENT)
        self.assertEqual(indent_of(trim_blank_lines(INDENTED_FRAGMENT).split("\n")[0]), 4)

    def test_drops_blank_lines_around_indented_fragment(self):
        padded = "\n   \n" + INDENTED_FRAGMENT + "\n\n  \n"
        self.assertEqual(trim_blank_lines(padded), INDENTED_FRAGMENT)

    def test_single_indented_line_keeps_its_indent(self):
        self.assertEqual(trim_blank_lines("    x = 1"), "    x = 1")

    def test_all_blank_collapses_to_empty(self):
        # app.py relies on this to still return "Paste some code first."
        for blank in ("", "\n", "   \n  \n", "\t\n"):
            self.assertEqual(trim_blank_lines(blank), "")

    def test_keeps_interior_blank_lines(self):
        code = "def a():\n    pass\n\ndef b():\n    pass"
        self.assertEqual(trim_blank_lines(code), code)

    def test_keeps_tabs_and_trailing_spaces_on_code_lines(self):
        code = "\tif x:\n\t\treturn 1   "
        self.assertEqual(trim_blank_lines(code), code)

    def test_differs_from_strip_on_indented_first_line(self):
        # Regression guard: if someone "simplifies" this back to .strip(), fail.
        self.assertNotEqual(
            trim_blank_lines(INDENTED_FRAGMENT), INDENTED_FRAGMENT.strip()
        )


class BuildUserMessage(unittest.TestCase):
    def test_fragment_indent_survives_into_the_prompt(self):
        msg = build_user_message(INDENTED_FRAGMENT, "indentation error")
        fenced = msg.split("```")[1].strip("\n")
        self.assertEqual(fenced, INDENTED_FRAGMENT)
        self.assertEqual(indent_of(fenced.split("\n")[0]), 4)

    def test_issue_prose_is_still_stripped(self):
        msg = build_user_message("x = 1", "  it crashes  ")
        self.assertTrue(msg.rstrip().endswith("it crashes"))


class FakeResponse:
    status_code, ok, text = 200, True, ""

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "stubbed reply"}}]}


class TutorEndpoint(unittest.TestCase):
    """End-to-end through the real route, with only the network faked."""

    def setUp(self):
        self.sent = {}
        self._real_post = tutor_app.requests.post

        def fake_post(url, headers=None, json=None, timeout=None):
            self.sent["payload"] = json
            return FakeResponse()

        tutor_app.requests.post = fake_post
        tutor_app.os.environ.setdefault("GROQ_API_KEY", "test-key")
        self.client = tutor_app.app.test_client()

    def tearDown(self):
        tutor_app.requests.post = self._real_post

    def post(self, **kwargs):
        payload = {"code": "x = 1", "issue": "broken", "attempt": 0}
        payload.update(kwargs)
        return self.client.post("/tutor", json=payload)

    def code_sent_to_groq(self):
        user_msg = self.sent["payload"]["messages"][1]["content"]
        return user_msg.split("```")[1].strip("\n")

    def test_indented_fragment_reaches_groq_with_line_one_intact(self):
        response = self.post(code=INDENTED_FRAGMENT, issue="indentation error")
        self.assertEqual(response.status_code, 200)

        arrived = self.code_sent_to_groq()
        self.assertEqual(arrived, INDENTED_FRAGMENT)
        self.assertEqual(indent_of(arrived.split("\n")[0]), 4, "line 1 lost its indent")

    def test_blank_only_code_is_still_rejected(self):
        response = self.post(code="   \n  \n")
        self.assertEqual(response.status_code, 400)

    def test_attempt_maps_to_level_and_prompt(self):
        for attempt, expected in [(0, 1), (1, 2), (2, 2), (3, 3), (99, 3), (-5, 1)]:
            with self.subTest(attempt=attempt):
                body = self.post(attempt=attempt).get_json()
                self.assertEqual(body["level"], expected)

                system_prompt = self.sent["payload"]["messages"][0]["content"]
                embedded = [
                    lvl for lvl, txt in LEVEL_INSTRUCTIONS.items() if txt in system_prompt
                ]
                self.assertEqual(embedded, [expected])

    def test_response_contract_is_stable(self):
        body = self.post().get_json()
        self.assertEqual(set(body), {"reply", "level", "attempt"})

    def test_level_for_attempt_handles_junk(self):
        for junk in (None, "abc", [], {}):
            self.assertEqual(level_for_attempt(junk), 1)


# ---------------------------------------------------------------------------
# Rule-based detectors
#
# Every detector gets both halves: cases that must fire, and correct code that
# must stay silent. The silent half is the important one — a detector that
# fires on working code aims the tutor at a bug the student does not have, and
# the student cannot see the tag to know why the questions went strange.
# ---------------------------------------------------------------------------


def tags_for(source):
    """Run the detectors over a dedented snippet."""
    return detect_bugs(textwrap.dedent(source).strip("\n"))


class AssignInConditionDetector(unittest.TestCase):
    def test_fires_on_single_equals_in_if(self):
        self.assertIn(TAG_ASSIGN_IN_CONDITION, tags_for("""
            score = 0
            if score = 100:
                print("full marks")
        """))

    def test_fires_on_single_equals_in_while(self):
        self.assertIn(TAG_ASSIGN_IN_CONDITION, tags_for("""
            count = 0
            while count = 0:
                print("looping")
        """))

    def test_fires_on_single_equals_in_elif(self):
        self.assertIn(TAG_ASSIGN_IN_CONDITION, tags_for("""
            x = 1
            if x > 5:
                print("big")
            elif x = 2:
                print("two")
        """))

    def test_silent_on_correct_equality_test(self):
        self.assertEqual(tags_for("""
            score = 100
            if score == 100:
                print("full marks")
        """), [])

    def test_silent_on_other_comparison_operators(self):
        for op in ("==", "!=", "<=", ">=", "<", ">"):
            with self.subTest(op=op):
                self.assertEqual(tags_for("x = 1\nif x %s 5:\n    print(x)" % op), [])

    def test_silent_on_plain_assignment_outside_a_condition(self):
        self.assertEqual(tags_for("total = 10\nprint(total)"), [])

    def test_keyword_argument_is_not_mistaken_for_a_comparison(self):
        # A different syntax error (no colon) on a line whose only `=` is a
        # keyword argument. Wrong tag would be worse than no tag.
        self.assertNotIn(TAG_ASSIGN_IN_CONDITION, tags_for("""
            if print(sep="-")
                pass
        """))

    def test_equals_inside_a_string_literal_is_ignored(self):
        self.assertNotIn(TAG_ASSIGN_IN_CONDITION, tags_for("""
            name = "a"
            if name == "x = y"
                print(name)
        """))


class IndentationDetector(unittest.TestCase):
    def test_fires_when_a_block_body_is_missing_its_indent(self):
        self.assertEqual(tags_for("""
            def greet():
            print("hello")
        """), [TAG_INDENTATION])

    def test_fires_on_an_unexpected_indent(self):
        self.assertEqual(tags_for("""
            x = 1
              y = 2
        """), [TAG_INDENTATION])

    def test_fires_on_mixed_tabs_and_spaces(self):
        mixed = "if True:\n\tx = 1\n        y = 2\n"
        self.assertEqual(detect_bugs(mixed), [TAG_TABS_AND_SPACES])

    def test_silent_on_correctly_indented_code(self):
        self.assertEqual(tags_for("""
            def greet(name):
                if name:
                    print("hello", name)
                else:
                    print("hello")

            greet("Sam")
        """), [])

    def test_silent_on_a_consistently_tab_indented_program(self):
        self.assertEqual(detect_bugs("def greet():\n\tprint('hi')\n\ngreet()\n"), [])


class MissingReturnDetector(unittest.TestCase):
    def test_fires_when_the_result_is_assigned(self):
        self.assertIn(TAG_MISSING_RETURN, tags_for("""
            def add(a, b):
                result = a + b

            answer = add(3, 4)
            print(answer)
        """))

    def test_fires_when_the_result_is_passed_straight_to_print(self):
        self.assertIn(TAG_MISSING_RETURN, tags_for("""
            def add(a, b):
                result = a + b

            print(add(3, 4))
        """))

    def test_fires_on_a_bare_expression_body(self):
        # def double(n): n * 2 — computes and discards.
        self.assertIn(TAG_MISSING_RETURN, tags_for("""
            def double(n):
                n * 2

            x = double(3)
        """))

    def test_silent_when_the_function_returns(self):
        self.assertEqual(tags_for("""
            def add(a, b):
                result = a + b
                return result

            answer = add(3, 4)
            print(answer)
        """), [])

    def test_silent_on_a_print_only_procedure(self):
        self.assertEqual(tags_for("""
            def greet(name):
                print("hello", name)

            greet("Sam")
        """), [])

    def test_silent_when_a_procedure_result_is_assigned(self):
        # Deliberately conservative: a body of nothing but calls is a procedure,
        # and we do not second-guess someone assigning its None.
        self.assertEqual(tags_for("""
            def show_menu():
                print("1. Add")
                print("2. Quit")

            x = show_menu()
        """), [])

    def test_silent_when_the_call_discards_the_result(self):
        self.assertEqual(tags_for("""
            def add(a, b):
                result = a + b

            add(3, 4)
        """), [])

    def test_silent_on_a_generator(self):
        self.assertEqual(tags_for("""
            def counter(n):
                total = 0
                while total < n:
                    yield total
                    total = total + 1

            c = counter(3)
        """), [])

    def test_silent_on_a_stub(self):
        for body in ("pass", "..."):
            with self.subTest(body=body):
                self.assertEqual(tags_for("def todo():\n    %s\n\nx = todo()" % body), [])

    def test_silent_when_the_function_raises(self):
        self.assertEqual(tags_for("""
            def fail():
                message = "not implemented"
                raise ValueError(message)

            x = fail()
        """), [])

    def test_silent_on_methods(self):
        # Resolving obj.method() back to a class is guesswork, so we do not try.
        self.assertEqual(tags_for("""
            class Basket:
                def total(self, items):
                    subtotal = sum(items)

            b = Basket()
            x = b.total([1, 2])
        """), [])


class ScopeDetector(unittest.TestCase):
    def test_fires_when_a_local_is_read_at_module_level(self):
        self.assertIn(TAG_SCOPE, tags_for("""
            def setup():
                total = 0

            setup()
            print(total)
        """))

    def test_fires_when_one_function_reads_another_functions_local(self):
        self.assertIn(TAG_SCOPE, tags_for("""
            def setup():
                total = 0

            def show():
                print(total)
        """))

    def test_silent_when_the_name_is_also_bound_at_module_level(self):
        self.assertEqual(tags_for("""
            total = 0

            def setup():
                total = 5

            print(total)
        """), [])

    def test_silent_on_a_global_declaration(self):
        self.assertEqual(tags_for("""
            total = 0

            def setup():
                global total
                total = 5

            setup()
            print(total)
        """), [])

    def test_silent_on_a_closure_reading_an_enclosing_local(self):
        self.assertEqual(tags_for("""
            def outer():
                total = 0

                def inner():
                    print(total)

                inner()

            outer()
        """), [])

    def test_silent_on_parameters_used_inside_their_own_function(self):
        self.assertEqual(tags_for("""
            def greet(name):
                print("hello", name)

            greet("Sam")
        """), [])

    def test_silent_when_a_star_import_could_have_bound_the_name(self):
        # We no longer know what is bound at module level, so we say nothing.
        self.assertEqual(tags_for("""
            from math import *

            def area(r):
                tau = 6.28

            print(tau)
        """), [])

    def test_silent_on_a_shadowed_builtin(self):
        self.assertEqual(tags_for("""
            def collect():
                list = [1, 2]
                return list

            print(list)
        """), [])

    def test_silent_on_instance_attributes(self):
        self.assertEqual(tags_for("""
            class Cart:
                def __init__(self):
                    self.total = 0

            c = Cart()
            print(c.total)
        """), [])

    def test_silent_on_an_except_alias(self):
        self.assertEqual(tags_for("""
            try:
                value = int("x")
            except ValueError as err:
                print(err)
        """), [])

    def test_silent_on_a_module_level_comprehension_variable(self):
        self.assertEqual(tags_for("""
            nums = [1, 2, 3]
            squares = [n * n for n in nums]
            print(squares)
        """), [])


class InputWithoutCastDetector(unittest.TestCase):
    def test_fires_on_a_numeric_comparison(self):
        self.assertIn(TAG_INPUT_NO_CAST, tags_for("""
            age = input("How old are you? ")
            if age > 18:
                print("adult")
        """))

    def test_fires_on_subtraction(self):
        self.assertIn(TAG_INPUT_NO_CAST, tags_for("""
            n = input("Number: ")
            print(n - 1)
        """))

    def test_fires_on_multiplication_by_a_number(self):
        self.assertIn(TAG_INPUT_NO_CAST, tags_for("""
            price = input("Price: ")
            total = price * 2
            print(total)
        """))

    def test_fires_when_passed_to_range(self):
        self.assertIn(TAG_INPUT_NO_CAST, tags_for("""
            count = input("How many? ")
            for i in range(count):
                print(i)
        """))

    def test_fires_on_equality_against_a_number(self):
        self.assertIn(TAG_INPUT_NO_CAST, tags_for("""
            age = input("Age: ")
            if age == 18:
                print("exactly eighteen")
        """))

    def test_silent_when_the_text_is_used_as_text(self):
        self.assertEqual(tags_for("""
            name = input("Your name: ")
            print("Hello", name)
        """), [])

    def test_silent_when_wrapped_in_int_at_the_call(self):
        self.assertEqual(tags_for("""
            age = int(input("Age: "))
            if age > 18:
                print("adult")
        """), [])

    def test_silent_when_converted_on_the_next_line(self):
        self.assertEqual(tags_for("""
            age = input("Age: ")
            age = int(age)
            if age > 18:
                print("adult")
        """), [])

    def test_silent_on_string_concatenation(self):
        self.assertEqual(tags_for("""
            name = input("Name: ")
            print(name + "!")
        """), [])

    def test_silent_on_alphabetical_ordering(self):
        # `if answer > "m":` is legal and occasionally intended.
        self.assertEqual(tags_for("""
            answer = input("Letter: ")
            if answer > "m":
                print("late in the alphabet")
        """), [])

    def test_silent_on_comparison_against_a_boolean(self):
        self.assertEqual(tags_for("""
            flag = input("yes/no: ")
            if flag == True:
                print("yes")
        """), [])


class OffByOneDetector(unittest.TestCase):
    def test_fires_on_range_len_plus_one(self):
        self.assertIn(TAG_OFF_BY_ONE_RANGE, tags_for("""
            names = ["Ana", "Bo"]
            for i in range(len(names) + 1):
                print(names[i])
        """))

    def test_fires_on_the_range_zero_spelling(self):
        self.assertIn(TAG_OFF_BY_ONE_RANGE, tags_for("""
            names = ["Ana", "Bo"]
            for i in range(0, len(names) + 1):
                print(names[i])
        """))

    def test_fires_when_indexing_one_ahead(self):
        self.assertIn(TAG_OFF_BY_ONE_INDEX, tags_for("""
            nums = [1, 2, 3]
            for i in range(len(nums)):
                print(nums[i + 1])
        """))

    def test_silent_on_a_correct_index_loop(self):
        self.assertEqual(tags_for("""
            names = ["Ana", "Bo"]
            for i in range(len(names)):
                print(names[i])
        """), [])

    def test_silent_on_correct_pairwise_comparison(self):
        # The near-miss that matters: range(len(nums) - 1) with nums[i + 1].
        self.assertEqual(tags_for("""
            nums = [1, 2, 3]
            for i in range(len(nums) - 1):
                if nums[i] > nums[i + 1]:
                    print("out of order")
        """), [])

    def test_silent_when_the_extra_pass_indexes_nothing(self):
        self.assertEqual(tags_for("""
            names = ["Ana", "Bo"]
            for i in range(len(names) + 1):
                print(i)
        """), [])

    def test_silent_when_a_different_sequence_is_indexed(self):
        self.assertEqual(tags_for("""
            names = ["Ana", "Bo"]
            scores = [1, 2, 3, 4]
            for i in range(len(names) + 1):
                print(scores[i])
        """), [])

    def test_silent_on_direct_iteration(self):
        self.assertEqual(tags_for("""
            names = ["Ana", "Bo"]
            for name in names:
                print(name)
        """), [])


class DetectorsStaySilentOnWorkingPrograms(unittest.TestCase):
    """Whole correct programs of the kind BICT131 actually sets."""

    PROGRAMS = [
        """
        def average(marks):
            total = 0
            for mark in marks:
                total = total + mark
            return total / len(marks)

        marks = [55, 70, 91]
        print("Average:", average(marks))
        """,
        """
        name = input("What is your name? ")
        age = int(input("How old are you? "))
        if age >= 18:
            print(name, "can enrol")
        else:
            print(name, "is too young")
        """,
        """
        def count_vowels(word):
            count = 0
            for letter in word:
                if letter in "aeiou":
                    count = count + 1
            return count

        print(count_vowels("programming"))
        """,
        """
        scores = [4, 8, 15]
        highest = scores[0]
        for i in range(1, len(scores)):
            if scores[i] > highest:
                highest = scores[i]
        print("Highest:", highest)
        """,
    ]

    def test_no_tags_on_correct_programs(self):
        for source in self.PROGRAMS:
            with self.subTest(source=source.strip().split("\n")[0]):
                self.assertEqual(tags_for(source), [])


class DetectBugsIsTotal(unittest.TestCase):
    """Detection is optional context — it must never be the thing that breaks."""

    def test_junk_input_returns_no_tags_and_does_not_raise(self):
        for junk in ("", "   ", "\x00", "????", "def", ")("):
            with self.subTest(junk=junk):
                self.assertIsInstance(detect_bugs(junk), list)

    def test_result_is_always_a_list_of_strings(self):
        result = tags_for("def add(a, b):\n    r = a + b\n\nx = add(1, 2)")
        self.assertTrue(all(isinstance(tag, str) for tag in result))


# ---------------------------------------------------------------------------
# Tag injection — the tag informs the hint, it does not loosen the ladder
# ---------------------------------------------------------------------------


class DetectorTagInThePrompt(unittest.TestCase):
    def test_no_tags_is_byte_identical_to_the_pre_detector_prompt(self):
        for attempt, level in [(0, 1), (1, 2), (3, 3)]:
            with self.subTest(attempt=attempt):
                expected = BASE_RULES + "\n" + LEVEL_INSTRUCTIONS[level]
                self.assertEqual(build_system_prompt(attempt), expected)
                self.assertEqual(build_system_prompt(attempt, None), expected)
                self.assertEqual(build_system_prompt(attempt, []), expected)

    def test_tag_text_appears_as_a_bullet(self):
        prompt = build_system_prompt(0, [TAG_MISSING_RETURN])
        self.assertIn("- " + TAG_MISSING_RETURN, prompt)

    def test_every_tag_is_rendered(self):
        tags = [TAG_SCOPE, TAG_OFF_BY_ONE_RANGE]
        prompt = build_system_prompt(0, tags)
        for tag in tags:
            self.assertIn(tag, prompt)

    def test_framing_marks_the_tag_as_a_lead_and_forbids_mentioning_it(self):
        prompt = build_system_prompt(0, [TAG_SCOPE])
        self.assertIn("lead, not a verdict", prompt)
        self.assertIn("Never mention this scan", prompt)
        self.assertIn("does not change how much you are allowed to reveal", prompt)

    def test_level_instruction_stays_last(self):
        # Ordering is the other half of protecting the ladder: the level rules
        # must be the most recent thing the model reads, not the diagnosis.
        prompt = build_system_prompt(0, [TAG_MISSING_RETURN])
        self.assertTrue(prompt.endswith(LEVEL_INSTRUCTIONS[1]))
        self.assertLess(prompt.index(BASE_RULES), prompt.index(TAG_MISSING_RETURN))
        self.assertLess(prompt.index(TAG_MISSING_RETURN), prompt.index(LEVEL_INSTRUCTIONS[1]))

    def test_a_tag_does_not_change_which_level_instruction_is_used(self):
        for attempt, expected in [(0, 1), (1, 2), (2, 2), (3, 3), (99, 3), (-5, 1)]:
            with self.subTest(attempt=attempt):
                prompt = build_system_prompt(attempt, [TAG_MISSING_RETURN])
                embedded = [
                    lvl for lvl, txt in LEVEL_INSTRUCTIONS.items() if txt in prompt
                ]
                self.assertEqual(embedded, [expected])

    def test_level_one_keeps_its_anti_leak_rules_with_a_tag_present(self):
        prompt = build_system_prompt(0, [TAG_MISSING_RETURN])
        self.assertIn("ONE short question", prompt)
        self.assertIn("Do not name the concept behind the bug.", prompt)

    def test_tags_never_name_an_identifier_from_the_students_code(self):
        # The 17 Aug lesson: give the model the name and it puts the name in
        # the Level 1 question. Tags describe the concept only.
        #
        # The identifiers below are deliberately not ordinary English words. A
        # student variable named `result` or `total` would appear to collide
        # with tag prose like "a function's result" — that is the English noun
        # doing its job, not the detector echoing their code, and a test that
        # could not tell the two apart would be testing nothing.
        snippets = [
            """
            def combine_marks(a, b):
                subtotal = a + b

            outcome = combine_marks(3, 4)
            """,
            """
            def configure():
                threshold = 0

            configure()
            print(threshold)
            """,
        ]
        identifiers = ("combine_marks", "subtotal", "outcome", "configure", "threshold")
        for source in snippets:
            fired = tags_for(source)
            self.assertTrue(fired, "snippet should have fired a detector")
            for tag in fired:
                for identifier in identifiers:
                    with self.subTest(tag=tag, identifier=identifier):
                        self.assertNotIn(identifier, tag)

    def test_tags_never_name_a_line_number(self):
        # Same reasoning: a line number pinpoints as hard as a name does.
        source = """
            names = ["Ana", "Bo"]
            for i in range(len(names) + 1):
                print(names[i])
        """
        for tag in tags_for(source):
            self.assertFalse(
                any(char.isdigit() for char in tag),
                "tag should carry no line or position number: %r" % tag,
            )


class DetectorsThroughTheEndpoint(unittest.TestCase):
    """The detector layer, end to end, with only the network faked."""

    MISSING_RETURN_CODE = (
        "def add(a, b):\n"
        "    result = a + b\n"
        "\n"
        "answer = add(3, 4)\n"
        "print(answer)"
    )

    def setUp(self):
        self.sent = {}
        self._real_post = tutor_app.requests.post

        def fake_post(url, headers=None, json=None, timeout=None):
            self.sent["payload"] = json
            return FakeResponse()

        tutor_app.requests.post = fake_post
        tutor_app.os.environ.setdefault("GROQ_API_KEY", "test-key")
        self.client = tutor_app.app.test_client()

    def tearDown(self):
        tutor_app.requests.post = self._real_post

    def post(self, **kwargs):
        payload = {"code": self.MISSING_RETURN_CODE, "issue": "prints None", "attempt": 0}
        payload.update(kwargs)
        return self.client.post("/tutor", json=payload)

    def system_prompt(self):
        return self.sent["payload"]["messages"][0]["content"]

    def test_tag_reaches_the_system_prompt(self):
        self.post()
        self.assertIn(TAG_MISSING_RETURN, self.system_prompt())

    def test_a_firing_detector_still_gets_a_level_one_instruction(self):
        self.post(attempt=0)
        prompt = self.system_prompt()
        self.assertIn(LEVEL_INSTRUCTIONS[1], prompt)
        self.assertNotIn(LEVEL_INSTRUCTIONS[2], prompt)
        self.assertNotIn(LEVEL_INSTRUCTIONS[3], prompt)

    def test_a_firing_detector_does_not_shift_any_level(self):
        for attempt, expected in [(0, 1), (1, 2), (2, 2), (3, 3), (99, 3)]:
            with self.subTest(attempt=attempt):
                body = self.post(attempt=attempt).get_json()
                self.assertEqual(body["level"], expected)
                embedded = [
                    lvl
                    for lvl, txt in LEVEL_INSTRUCTIONS.items()
                    if txt in self.system_prompt()
                ]
                self.assertEqual(embedded, [expected])

    def test_response_contract_is_unchanged_when_a_detector_fires(self):
        body = self.post().get_json()
        self.assertEqual(set(body), {"reply", "level", "attempt"})

    def test_the_tag_is_never_sent_to_the_student(self):
        response = self.post()
        self.assertNotIn(TAG_MISSING_RETURN, response.get_data(as_text=True))
        self.assertNotIn("automatic scan", response.get_data(as_text=True))

    def test_clean_code_adds_no_detector_block_at_all(self):
        self.post(code="def add(a, b):\n    return a + b\n\nprint(add(1, 2))")
        prompt = self.system_prompt()
        self.assertNotIn("lead, not a verdict", prompt)
        self.assertEqual(prompt, BASE_RULES + "\n" + LEVEL_INSTRUCTIONS[1])

    def test_unparseable_code_still_gets_a_hint(self):
        response = self.post(code="if score = 100:\n    print('full')")
        self.assertEqual(response.status_code, 200)
        self.assertIn(TAG_ASSIGN_IN_CONDITION, self.system_prompt())


if __name__ == "__main__":
    unittest.main()
