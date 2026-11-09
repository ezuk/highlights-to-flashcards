"""Write notes: the vault helpers, the AI replies, and the whole path through the service."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import fakes
from helpers import TempHome

from h2f import collection, notes, notes_ai, prompts
from h2f.service import Service, ServiceError
from h2f.store import Store


class NamesTest(unittest.TestCase):
    def test_book_tag_drops_the_article_and_subtitle(self):
        self.assertEqual(notes.book_tag("The Art of War: The Oldest Military Treatise"), "#book/ArtOfWar")
        self.assertEqual(notes.book_tag("Pride and Prejudice"), "#book/PrideAndPrejudice")
        self.assertEqual(notes.hub_title("The Art of War: The Oldest"), "The Art of War (book)")

    def test_clean_tag_and_title(self):
        self.assertEqual(notes.clean_tag("book/Foo"), "#book/Foo")
        self.assertIsNone(notes.clean_tag("#1x"))
        self.assertEqual(notes.clean_title(" a/b: c? "), "a b c")
        self.assertEqual(notes.clean_title("[[ ]]"), "")


class LinkTest(unittest.TestCase):
    def test_first_mention_becomes_the_link(self):
        text = "#book/X\n\nIf you just wait, wait again.\n"
        self.assertEqual(notes.add_link(text, "wait"), "#book/X\n\nIf you just [[wait]], wait again.\n")

    def test_mention_keeps_its_case(self):
        self.assertEqual(notes.add_link("Just Wait.\n", "wait"), "Just [[Wait]].\n")

    def test_no_mention_goes_to_see_also(self):
        self.assertEqual(notes.add_link("Body\n", "wait"), "Body\n\nSee also: [[wait]]\n")
        self.assertEqual(notes.add_link("Body\n\nSee also: [[a]]\n", "wait"), "Body\n\nSee also: [[a]], [[wait]]\n")

    def test_quotes_code_links_and_urls_are_left_alone(self):
        text = "> wait in a quote\n\n`wait` and [[waiting]] and http://x.org/wait\n"
        self.assertTrue(notes.add_link(text, "wait").endswith("See also: [[wait]]\n"))
        self.assertTrue(notes.add_link(text, "wait").startswith(text.rstrip("\n")))

    def test_an_existing_link_is_kept(self):
        text = "Already [[Wait|waiting]] here.\n"
        self.assertEqual(notes.add_link(text, "wait"), text)

    def test_new_note_and_quotes(self):
        text = notes.new_note("#book/X", ["one", "two\nlines"], ["a", "b"])
        self.assertEqual(text, "#book/X\n\n> one\n\n> two\n> lines\n\n\n\nSee also: [[a]], [[b]]\n")
        self.assertEqual(notes.append_quotes("Body\n\nSee also: [[a]]\n", ["q"]), "Body\n\n> q\n\nSee also: [[a]]\n")

    def test_hub_note_lists_notes_once(self):
        text = notes.hub_note("#book/X", "", "What I remember.", ["a"])
        self.assertIn(notes.HUB_FIRST + "\n\nWhat I remember.", text)
        added = notes.hub_add(text + "\nSee also: [[z]]\n", ["a", "b"])
        self.assertIn("- [[a]]\n- [[b]]\n", added)
        self.assertTrue(added.rstrip().endswith("See also: [[z]]"))
        self.assertEqual(added.count("[[a]]"), 1)


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="h2f-vault-"))
        (self.root / ".obsidian").mkdir()
        (self.root / ".obsidian" / "community-plugins.json").write_text('["obsidian-advanced-uri"]')
        (self.root / "Templates").mkdir()
        (self.root / "Templates" / "skip.md").write_text("template")
        (self.root / "values.md").write_text("#habits\n\nWhat matters.\n")
        self.launched = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def vault(self, running=False, obsidian=None):
        def launch(args):
            self.launched.append(args)
            if obsidian:
                obsidian(args)
            return True
        return notes.Vault(self.root, launch=launch, obsidian_running=lambda: running, sleep=lambda _s: None)

    def test_reads_notes_and_checks(self):
        vault = self.vault()
        check = vault.check()
        self.assertTrue(check["ok"] and check["advanced_uri"])
        self.assertEqual(check["notes"], 1)
        self.assertEqual(vault.notes()[0]["tags"], ["#habits"])
        self.assertEqual(vault.find("VALUES"), "values.md")

    def test_create_refuses_a_title_in_use(self):
        vault = self.vault()
        self.assertEqual(vault.create("new one", "x"), "new one.md")
        with self.assertRaises(notes.VaultError):
            vault.create("Values", "x")

    def test_change_on_disk_when_obsidian_is_closed(self):
        vault = self.vault()
        before = vault.read("values.md")
        self.assertTrue(vault.change("values.md", before, before + "more\n"))
        self.assertEqual(vault.read("values.md"), before + "more\n")
        with self.assertRaises(notes.VaultError):
            vault.change("values.md", before, "stale")

    def test_change_through_obsidian_when_it_runs(self):
        written = {}

        def obsidian(args):
            # Stands in for Advanced URI writing the file
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(args[-1]).query)
            written.update(query)
            (self.root / query["filepath"][0]).write_text(query["data"][0])

        vault = self.vault(running=True, obsidian=obsidian)
        before = vault.read("values.md")
        vault.change("values.md", before, "changed")
        self.assertEqual(written["mode"], ["overwrite"])
        self.assertEqual(vault.read("values.md"), "changed")

    def test_obsidian_that_never_writes_is_reported(self):
        vault = self.vault(running=True)
        with self.assertRaises(notes.VaultError):
            vault.change("values.md", vault.read("values.md"), "changed")
        self.assertEqual(vault.read("values.md"), "#habits\n\nWhat matters.\n")


class RepliesTest(unittest.TestCase):
    def test_gather_keeps_known_titles_and_numbers(self):
        reply = ('{"ideas": [{"title": "A", "summary": "s", "highlights": [1, 9, 2, 2],'
                 ' "close_to": [{"note": "VALUES", "why": "w"}, {"note": "made up", "why": "w"}]},'
                 ' {"title": "B", "highlights": [7]}], "missed": []}')
        parsed, problem = notes_ai.parse_gather(reply, 3, ["values"])
        self.assertIsNone(problem)
        self.assertEqual(len(parsed["ideas"]), 1)
        self.assertEqual(parsed["ideas"][0]["highlights"], [1, 2])
        self.assertEqual(parsed["ideas"][0]["flags"], [{"kind": "close", "note": "values", "why": "w"}])

    def test_links_and_titles(self):
        links, _ = notes_ai.parse_links('{"links": [{"note": "values", "kind": "odd", "why": "w"}, {"note": "x"}]}',
                                        ["values"])
        self.assertEqual(links, [{"note": "values", "kind": "same idea", "why": "w"}])
        titles, _ = notes_ai.parse_titles('```json\n{"titles": ["values", "slow: down", "slow down"]}\n```', ["values"])
        self.assertEqual(titles, ["slow down"])
        self.assertIsNotNone(notes_ai.parse_titles("no json", [])[1])

    def test_likely_notes_match_telling_words(self):
        vault = [{"title": "cavalry", "text": "Horses win battles in open country."},
                 {"title": "siege", "text": "Never besiege walled cities, the worst of policies."},
                 {"title": "the", "text": "the the the and of"},
                 {"title": "hub", "text": "walled cities horses"}]
        found = notes_ai.likely_notes(vault, ["Attacking walled cities wastes an army", "horses"], per_text=1,
                                      skip=["hub"])
        self.assertEqual([n["title"] for n in found], ["siege", "cavalry"])
        messages = notes_ai.build_links({"title": "T", "author": "A"}, {"title": "i"}, [], [], None, "- siege",
                                        "p", full=found[:1])
        self.assertIn("<slipbox_notes>\n### siege\nNever besiege", messages[-1]["content"])

    def test_missed_answers_keep_a_new_summary(self):
        reply = '{"ideas": [], "missed": [{"number": 2, "summary": "Plainly put.", "close_to": []}, {"number": 9}]}'
        parsed, _ = notes_ai.parse_gather(reply, 0, [], 2)
        self.assertEqual(parsed["missed"], [{"number": 2, "summary": "Plainly put.", "flags": []}])

    def test_gather_sends_the_book_but_not_the_reflection(self):
        messages = notes_ai.build_gather({"title": "T", "author": "A"}, [], [], None, [], "#book/T", "p",
                                         book_text="## 1 · One\n\nThe text.")
        self.assertIn("<book>\n## 1 · One\n\nThe text.\n</book>", messages[-1]["content"])
        self.assertNotIn("memory", messages[-1]["content"])

    def test_book_text_marks_chapters_and_stops_where_the_reader_is(self):
        from h2f import epub
        book = epub.open_epub(Path(__file__).parent / "fixtures" / "books" / "pride-and-prejudice.epub")
        whole = book.book_text()
        self.assertIn("\n## Chapter I.\n", whole)
        self.assertIn("single man in possession of a good fortune", whole)
        self.assertEqual(book.book_text(0.5), whole[:len(whole) // 2])

    def test_note_prompts_load(self):
        loaded = prompts.notes_builtin()
        self.assertEqual(set(loaded), {"notes-ideas", "notes-links", "notes-titles"})


class NotesFlowTest(TempHome):
    def setUp(self):
        super().setUp()
        os.environ["H2F_OPEN_CMD"] = "true"
        self.restore = fakes.fake_ankiweb(collection.Collection)
        self.service = Service(store=Store(), sync_on_start=False)
        self.vault = self.tmp / "slipbox"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / "slow down.md").write_text("#book/Other\n\nSlowing down helps.\n")
        (self.vault / "values.md").write_text("What matters most.\n")

    def tearDown(self):
        self.service.stop(wait=1)
        self.service.collection.close()
        self.restore()
        os.environ.pop("H2F_OPEN_CMD", None)
        super().tearDown()

    def wait_for_jobs(self):
        deadline = time.time() + 20
        while self.service.notes._jobs and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.service.notes._jobs)

    def test_needs_a_vault(self):
        with self.assertRaises(ServiceError):
            self.service.notes.update_settings({"vault": str(self.tmp)})
        self.assertFalse(self.service.notes.setup()["vault"]["ok"])

    def test_setup_lists_the_note_prompts(self):
        setup = self.service.notes.setup()
        self.assertEqual([f["label"] for f in setup["files"]],
                         ["Gather highlights into ideas", "Find related notes", "Suggest titles"])
        self.assertTrue(setup["files"][1]["display"].endswith("Standard/Slipbox/links.md"))

    def test_from_book_to_linked_note(self):
        self.service.readwise_token(fakes.TOKEN)
        self.service._sync_thread.join(10)
        notes_service = self.service.notes
        notes_service.update_settings({"vault": str(self.vault)})
        book_id = next(b["id"] for b in self.service.books()["books"] if b["title"] == "Pride and Prejudice")

        notes_service.update_book(book_id, {"reflection": "Money and marriage."})
        notes_service.gather(book_id)
        self.wait_for_jobs()
        book = notes_service.book_view(book_id)
        self.assertEqual(book["tag"], "#book/PrideAndPrejudice")
        self.assertEqual(book["status"], "ready")
        idea = book["ideas"][0]
        self.assertEqual(idea["count"], 2)

        notes_service.idea_view(idea["id"])
        self.wait_for_jobs()
        view = notes_service.idea_view(idea["id"])
        self.assertEqual([r["note"] for r in view["related"]], ["slow down", "values"])
        self.assertTrue(view["titles"])

        made = notes_service.create_note(idea["id"], "a fortune wants a wife", ["slow down", "values"])
        self.assertEqual(made["problems"], [])
        text = (self.vault / "a fortune wants a wife.md").read_text()
        self.assertTrue(text.startswith("#book/PrideAndPrejudice\n\n> It is a truth universally acknowledged"))
        self.assertIn("See also: [[slow down]], [[values]]", text)
        self.assertIn("See also: [[a fortune wants a wife]]", (self.vault / "values.md").read_text())
        with self.assertRaises(ServiceError):
            notes_service.create_note(idea["id"], "values", [])

        with open(self.vault / "a fortune wants a wife.md", "a") as f:
            f.write("Like [[slow down]] and the [[missing one]].\n")
        (self.vault / "new.md").write_text("A fortune wants a wife, they say.\n")
        with open(self.vault / "a fortune wants a wife.md", "a") as f:
            f.write("Also [[new]].\n")
        writing = notes_service.written_view(made["note_id"])
        self.assertEqual(writing["missing"], ["missing one"])
        done = notes_service.written_done(made["note_id"])
        self.assertEqual(done["problems"], [])
        self.assertEqual((self.vault / "new.md").read_text(), "A fortune wants a wife, they say.\n".replace(
            "A fortune wants a wife", "[[A fortune wants a wife]]"))
        self.assertEqual((self.vault / "slow down.md").read_text().count("[[a fortune wants a wife]]"), 1)

        notes_service.hub(book_id)
        hub = (self.vault / "Pride and Prejudice (book).md").read_text()
        self.assertIn("Money and marriage.", hub)
        self.assertIn("- [[a fortune wants a wife]]", hub)
        book = notes_service.book_view(book_id)
        self.assertEqual(book["stats"]["notes"], 1)
        self.assertTrue(book["hub"]["exists"])


if __name__ == "__main__":
    unittest.main()
