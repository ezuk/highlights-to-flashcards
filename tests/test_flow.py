"""The whole path, through the app's service: connect, pick a book, write a card, save and sync."""

import sqlite3
import unittest

import fakes
from helpers import TempHome

from h2f import collection, keychain, paths
from h2f.service import Service, ServiceError
from h2f.store import Store


class FlowTest(TempHome):
    def setUp(self):
        super().setUp()
        self.restore = fakes.fake_ankiweb(collection.Collection)
        self.service = Service(store=Store(), sync_on_start=False)

    def tearDown(self):
        self.service.stop(wait=1)
        self.service.collection.close()
        self.restore()
        super().tearDown()

    def wait_for_sync(self):
        thread = self.service._sync_thread
        if thread is not None:
            thread.join(10)
        self.assertFalse(self.service._syncing)

    def connect(self):
        self.service.readwise_token(fakes.TOKEN)
        self.wait_for_sync()
        self.service.ankiweb_login("tester@example.com", "test-password")
        self.wait_for_sync()

    def test_sources_start_unconnected(self):
        sources = self.service.state()["sources"]
        self.assertFalse(sources["ready"])
        self.assertFalse(sources["readwise"]["connected"])
        self.assertEqual(sources["calibre"]["books"], 2)

    def test_wrong_token_and_password_are_refused(self):
        with self.assertRaises(ServiceError):
            self.service.readwise_token("wrong")
        with self.assertRaises(ServiceError):
            self.service.ankiweb_login("tester@example.com", "wrong")
        self.assertIsNone(keychain.get(keychain.READWISE))
        self.assertIsNone(keychain.get(keychain.ANKIWEB))

    def test_connect_then_save_a_card(self):
        self.connect()
        sources = self.service.state()["sources"]
        self.assertTrue(sources["ready"], sources)
        self.assertEqual(sources["readwise"]["highlights"], 4)

        rows = {row["title"]: row for row in self.service.books()["books"]}
        book_id = rows["Pride and Prejudice"]["id"]
        book = self.service.open_book(book_id)
        first = book["highlights"][0]
        self.assertIn("single man", first["text"])

        card = self.service.add_card(book_id, first["id"])["card"]
        self.service.update_card(card["id"], front="Who must be in want of a wife?",
                                 back="A single man in possession of a good fortune")
        preview = self.service.save_preview(book_id)
        self.assertEqual(preview["saveCount"], 1)
        self.assertEqual(preview["saveBtn"], "Save and sync")

        result = self.service.save_book(book_id)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["done"]["doneTitle"], "1 card in your collection")

        # The highlight now counts as reviewed, and the collection has the card with its context
        rows = {row["title"]: row for row in self.service.books()["books"]}
        self.assertEqual(rows["Pride and Prejudice"]["counts"]["reviewed"], 1)
        self.service.collection.close()
        db = sqlite3.connect(f"file:{paths.collection_path()}?mode=ro", uri=True)
        [(fields, usn)] = db.execute("SELECT flds, usn FROM notes").fetchall()
        db.close()
        front, back, quote, title, author, chapter, page, file, _position = fields.split("\x1f")
        self.assertEqual((front, title, chapter, page), ("Who must be in want of a wife?", "Pride and Prejudice",
                                                         "Chapter I.", "1"))
        self.assertEqual(file, "Pride and Prejudice - Jane Austen.epub")
        self.assertEqual(usn, 0)

    def test_save_without_ankiweb_waits_for_the_next_sync(self):
        self.service.readwise_token(fakes.TOKEN)
        self.wait_for_sync()
        book_id = self.service.books()["books"][0]["id"]
        book = self.service.open_book(book_id)
        card = self.service.add_card(book_id, book["highlights"][0]["id"])["card"]
        self.service.update_card(card["id"], front="Q?", back="A")
        result = self.service.save_book(book_id)
        self.assertEqual(result["status"], "added")
        self.assertIn("Log in to AnkiWeb", result["done"]["doneRows"][-1]["p"])

    def test_link_a_book_to_another_calibre_book(self):
        self.connect()
        rows = {row["title"]: row for row in self.service.books()["books"]}
        book_id = rows["Meditations"]["id"]
        self.service.open_book(book_id)
        self.assertIsNone(self.service.book(book_id)["calibre"]["id"])
        found = self.service.calibre_search("pride austen")["books"]
        self.assertEqual([b["id"] for b in found], [1])
        view = self.service.link_book(book_id, 1)
        self.assertEqual(view["calibre"]["id"], 1)
        self.assertTrue(view["calibre"]["picked"])
        self.assertEqual(self.service.link_book(book_id, None)["calibre"]["id"], None)


if __name__ == "__main__":
    unittest.main()
