from __future__ import annotations

import datetime as dt
import unittest

from rigops import mine_group

from tests.test_mine import NOW

LINT = "no, always run the lint step before opening a pull request"


def _c(session, text, days=0):
    return mine_group.Correction(
        session=session, project="-p", ts=NOW + dt.timedelta(days=days), text=text)


class TokensTests(unittest.TestCase):
    def test_lowercases_and_returns_a_set(self):
        self.assertEqual(mine_group.tokens("Kiwi KIWI kiwi Lemon"), frozenset({"kiwi", "lemon"}))

    def test_author_slang_is_not_a_stopword(self):
        slang = frozenset({"tio", "tío", "mate", "man"})
        self.assertEqual(mine_group.tokens("tio tío mate man"), slang)

    def test_drops_short_words_and_stopwords_in_both_languages(self):
        self.assertEqual(mine_group.tokens("it is the kiwi and que los mango"),
                         frozenset({"kiwi", "mango"}))

    def test_keeps_accents_digits_and_underscores(self):
        self.assertEqual(mine_group.tokens("canción año_2 v12 ñandú"),
                         frozenset({"canción", "año_2", "v12", "ñandú"}))

    def test_empty_and_punctuation_only_text_has_no_tokens(self):
        for text in ("", "   ", "!!! ... ???", "a b c"):
            with self.subTest(text=text):
                self.assertEqual(mine_group.tokens(text), frozenset())

    def test_punctuation_splits_words(self):
        self.assertEqual(mine_group.tokens("kiwi,lemon;mango-papaya"),
                         frozenset({"kiwi", "lemon", "mango", "papaya"}))


class GroupEdgeTests(unittest.TestCase):
    def test_no_items_and_one_item_make_no_group(self):
        self.assertEqual(mine_group.group([]), [])
        self.assertEqual(mine_group.group([_c("s1", LINT)]), [])

    def test_the_same_text_repeated_inside_one_session_is_not_a_group(self):
        self.assertEqual(mine_group.group([_c("s1", LINT), _c("s1", LINT)]), [])

    def test_identical_text_across_two_sessions_is_one_group(self):
        groups = mine_group.group([_c("s1", LINT), _c("s2", LINT)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 2)
        self.assertEqual(len(groups[0].members), 2)

    def test_a_correction_under_the_token_floor_never_joins(self):
        short = "no, kiwi lemon"
        self.assertLess(len(mine_group.tokens(short)), mine_group.MIN_TOKENS)
        self.assertEqual(mine_group.group([_c("s1", short), _c("s2", short)]), [])
        groups = mine_group.group([_c("s1", LINT), _c("s2", LINT), _c("s3", short)])
        self.assertEqual({m.session for m in groups[0].members}, {"s1", "s2"})

    def test_a_stopword_only_correction_is_unusable(self):
        text = "please just do that now"
        self.assertEqual(mine_group.group([_c("s1", text), _c("s2", text)]), [])

    def test_threshold_is_inclusive(self):
        a = _c("s1", "kiwi lemon mango")
        b = _c("s2", "kiwi lemon papaya")
        self.assertEqual(len(mine_group.group([a, b], threshold=0.5)), 1)
        self.assertEqual(mine_group.group([a, b], threshold=0.51), [])

    def test_disjoint_texts_do_not_group(self):
        self.assertEqual(mine_group.group([_c("s1", "kiwi lemon mango"),
                                           _c("s2", "papaya guava cherry")]), [])

    def test_min_sessions_one_keeps_a_lone_correction(self):
        groups = mine_group.group([_c("s1", LINT)], min_sessions=1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sessions, 1)

    def test_min_sessions_three_rejects_a_pair(self):
        self.assertEqual(mine_group.group([_c("s1", LINT), _c("s2", LINT)], min_sessions=3), [])

    def test_the_input_is_not_reordered_or_consumed(self):
        items = [_c("s2", LINT), _c("s1", LINT)]
        mine_group.group(items)
        self.assertEqual([i.session for i in items], ["s2", "s1"])

    def test_groups_sort_by_sessions_then_members_then_earliest(self):
        big = [_c(f"b{n}", "kiwi lemon mango papaya") for n in range(3)]
        wide = [_c(s, "guava cherry banana grape", days=9) for s in ("w1", "w1", "w2", "w2")]
        late = [_c("l1", "plum apricot nectarine peach", days=5),
                _c("l2", "plum apricot nectarine peach", days=5)]
        early = [_c("e1", "fig date olive quince", days=1),
                 _c("e2", "fig date olive quince", days=1)]
        groups = mine_group.group(late + wide + early + big)
        self.assertEqual([(g.sessions, len(g.members)) for g in groups],
                         [(3, 3), (2, 4), (2, 2), (2, 2)])
        self.assertEqual([g.representative.ts for g in groups[2:]],
                         [NOW + dt.timedelta(days=1), NOW + dt.timedelta(days=5)])

    def test_representative_is_the_most_central_member(self):
        hub = _c("s1", "kiwi lemon mango papaya", days=5)
        left = _c("s2", "kiwi lemon mango guava")
        right = _c("s3", "kiwi lemon papaya cherry")
        groups = mine_group.group([left, right, hub], threshold=0.3)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].members), 3)
        # hub overlaps both others more than they overlap each other, though it is the latest
        self.assertIs(groups[0].representative, hub)

    def test_a_tie_on_centrality_goes_to_the_earliest(self):
        a = _c("s1", LINT, days=2)
        b = _c("s2", LINT, days=0)
        self.assertIs(mine_group.group([a, b])[0].representative, b)


if __name__ == "__main__":
    unittest.main()
