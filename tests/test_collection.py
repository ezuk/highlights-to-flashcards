"""The local Anki collection: adding cards, changing them, and the AnkiWeb sync rules."""

import unittest
from types import SimpleNamespace

from helpers import TempHome

from h2f import collection, keychain
from h2f.collection import Collection, CollectionError

CARD = {"front": "Who wants a wife?", "back": "A single man in possession of a good fortune.",
        "quote": "It is a truth universally acknowledged", "book": "Pride and Prejudice", "author": "Jane Austen",
        "chapter": "Chapter I.", "page": 1, "file": "pride.epub", "position": ""}


class CardsTest(TempHome):
    def setUp(self):
        super().setUp()
        self.col = Collection()

    def tearDown(self):
        self.col.close()
        super().tearDown()

    def test_new_cards_get_their_own_note_type_and_book_deck(self):
        notetype, parent, tags = self.col.defaults()
        [nid] = self.col.add_cards([CARD], notetype, parent, tags)
        anki = self.col._open()
        note = anki.get_note(nid)
        self.assertEqual(note.note_type()["name"], "Highlights to Flashcards")
        self.assertEqual(note["Book"], "Pride and Prejudice")
        self.assertEqual(note["File"], "pride.epub")
        self.assertEqual(note.tags, ["highlights-to-flashcards"])
        self.assertEqual(anki.decks.name(note.cards()[0].did), "Highlights::Pride and Prejudice")

    def test_cloze_cards_use_the_cloze_note_type(self):
        cloze = {**CARD, "front": "A single man must be in want of {{c1::a wife}}.", "back": ""}
        [nid] = self.col.add_cards([cloze], *self.col.defaults())
        self.assertEqual(self.col._open().get_note(nid).note_type()["name"], "Highlights to Flashcards Cloze")

    def test_reuses_the_koreader_plugin_note_type_when_there(self):
        anki = self.col._open()
        model = anki.models.copy(anki.models.by_name("Basic"), add=False)
        model["name"] = collection.PBANKI_NOTETYPE
        anki.models.add_dict(model)
        notetype, parent, tags = self.col.defaults()
        self.assertEqual((notetype, parent), (collection.PBANKI_NOTETYPE, collection.PBANKI_DECK))
        self.assertIn(collection.TAG, tags)

    def test_edit_suspend_and_delete(self):
        [nid] = self.col.add_cards([CARD], *self.col.defaults())
        self.assertEqual(self.col.apply("edit", nid, CARD["front"], CARD["back"], front="Who must want a wife?"),
                         "applied")
        self.assertEqual(self.col._open().get_note(nid)["Front"], "Who must want a wife?")
        # Changed elsewhere since: left alone
        self.assertEqual(self.col.apply("edit", nid, CARD["front"], CARD["back"], front="Again?"), "changed")
        self.assertEqual(self.col.apply("suspend", nid), "applied")
        self.assertEqual(self.col._open().get_note(nid).cards()[0].queue, -1)
        self.assertEqual(self.col.apply("delete", nid), "applied")
        self.assertEqual(self.col.apply("suspend", nid), "gone")

    def test_unsynced_counts_local_changes(self):
        before = self.col.unsynced()
        self.col.add_cards([CARD], *self.col.defaults())
        self.assertGreater(self.col.unsynced(), before)


class FakeAnki:
    """Records what a sync asked AnkiWeb to do."""

    def __init__(self, required, notes=0, unsynced=0):
        self.required = required
        self.calls = []
        self.db = SimpleNamespace(scalar=lambda sql: notes if "FROM notes" in sql and "usn" not in sql else unsynced)

    def sync_collection(self, auth, media):
        self.calls.append("sync")
        return SimpleNamespace(required=self.required, new_endpoint="")

    def close_for_full_sync(self):
        self.calls.append("close")

    def full_upload_or_download(self, auth, server_usn, upload):
        self.calls.append("upload" if upload else "download")

    def reopen(self, after_full_sync=False):
        self.calls.append("reopen")


class SyncRulesTest(TempHome):
    def setUp(self):
        super().setUp()
        from anki.sync_pb2 import SyncCollectionResponse

        self.required = SyncCollectionResponse.ChangesRequired
        keychain.put(keychain.ANKIWEB, {"hkey": "key", "endpoint": ""})

    def sync_with(self, fake):
        col = Collection()
        col._open = lambda: fake
        return col.sync()

    def test_normal_sync(self):
        fake = FakeAnki(self.required.NORMAL_SYNC)
        self.assertEqual(self.sync_with(fake), "synced")
        self.assertEqual(fake.calls, ["sync"])

    def test_never_uploads_a_whole_collection(self):
        fake = FakeAnki(self.required.FULL_UPLOAD, notes=10)
        with self.assertRaises(CollectionError):
            self.sync_with(fake)
        self.assertNotIn("upload", fake.calls)

    def test_full_sync_with_local_changes_stops(self):
        fake = FakeAnki(self.required.FULL_SYNC, notes=10, unsynced=3)
        with self.assertRaises(CollectionError):
            self.sync_with(fake)
        self.assertEqual(fake.calls, ["sync"])

    def test_full_sync_downloads_when_nothing_local_would_be_lost(self):
        for notes, unsynced in ((0, 5), (10, 0)):
            fake = FakeAnki(self.required.FULL_SYNC, notes=notes, unsynced=unsynced)
            self.assertEqual(self.sync_with(fake), "downloaded")
            self.assertEqual(fake.calls, ["sync", "close", "download", "reopen"])

    def test_first_download(self):
        fake = FakeAnki(self.required.FULL_DOWNLOAD)
        self.assertEqual(self.sync_with(fake), "downloaded")
        self.assertIn("download", fake.calls)

    def test_needs_login(self):
        keychain.forget(keychain.ANKIWEB)
        with self.assertRaises(CollectionError):
            Collection().sync()


if __name__ == "__main__":
    unittest.main()
