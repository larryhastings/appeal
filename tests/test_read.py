def preload_local_appeal():
    """
    Pre-load the local "appeal" module, to preclude finding
    an already-installed one on the path.
    """
    from pathlib import Path
    import sys
    appeal_dir = Path(sys.argv[0]).resolve().parent
    while True:
        appeal_init = appeal_dir / "appeal" / "__init__.py"
        if appeal_init.is_file():
            break
        appeal_dir = appeal_dir.parent
    sys.path.insert(1, str(appeal_dir))
    import appeal
    return appeal_dir

appeal_dir = preload_local_appeal()

import appeal
import big.all as big
import datetime
from dataclasses import dataclass
import csv
import os
import perky
import tomli
import unittest

os.chdir(appeal_dir / "tests")




class TestReadMapping(unittest.TestCase):

    def test_perky_basics(self):
        app = appeal.Appeal()

        def darth_vader(darth, vader):
            return (darth_vader, darth, vader)

        @app.unnested()
        def platformer(spyro, sparx):
            return (platformer, spyro, sparx)

        def test(a, b, c, d, e:darth_vader, f:platformer):
            return (test, a, b, c, d, e, f)

        mapping = perky.load("read_corpus/perkytest1.pky")
        got = app.read_mapping(test, mapping)

        expected = (
            test,
            "yes it's a",
            "oh my it's b",
            "\nc has a whole bunch of text!\nit's multi-line!\n   this line is indented!\n",
            ' I think d is quoted! ',
            (
                darth_vader,
                'Join me',
                'And together we will rule the galaxy',
            ),
            (
                platformer,
                'Race you to Dragon Shores!',
                'Bzzzz, bzz-bzzzzz-bz-bz-BZZZZZ-bzz-bzz!',
            )
        )

        self.assertEqual(expected, got)

    def test_perky_multioption_1(self):
        app = appeal.Appeal()
        def darth_vader(darth, vader):
            return (darth_vader, darth, vader)

        @app.unnested()
        def platformer(spyro, sparx):
            return (platformer, spyro, sparx)

        class test(appeal.MultiOption):
            def init(self, default):
                self.values = []
            def option(self, a, b, c, d, e:darth_vader, f:platformer):
                self.values.append((self.__class__, a, b, c, d, e, f))
            def render(self):
                return self.values

        mapping = perky.load("read_corpus/multiperky1.pky", root=[])
        got = app.read_mapping(test, mapping)

        expected = [
            (
                test,
                "yes it's a",
                "oh my it's b",
                "\nc has a whole bunch of text!\nit's multi-line!\n   this line is indented!\n",
                ' I think d is quoted! ',
                (
                    darth_vader,
                    'Join me',
                    'And together we will rule the galaxy',
                ),
                (
                    platformer,
                    'Race you to Dragon Shores!',
                    'Bzzzz, bzz-bzzzzz-bz-bz-BZZZZZ-bzz-bzz!',
                ),
            ),

            (
                test,
                "SECOND yes it's a",
                "SECOND oh my it's b",
                "\nSECOND\nc has a whole bunch of text!\nit's multi-line!\n   this line is indented!\n",
                ' SECOND I think d is quoted! ',
                (
                    darth_vader,
                    'SECOND Join me',
                    'SECOND And together we will rule the galaxy',
                ),
                (
                    platformer,
                    'SECOND Race you to Dragon Shores!',
                    'SECOND Bzzzz, bzz-bzzzzz-bz-bz-BZZZZZ-bzz-bzz!',
                )
            )
        ]
        self.assertEqual(expected, got)


    def test_perky_multioption_2(self):
        app = appeal.Appeal()

        class platformer(appeal.MultiOption):
            def init(self, default):
                self.values = []
            def option(self, spyro, sparx):
                self.values.append((self.__class__, spyro, sparx))
            def render(self):
                return self.values


        def test(a, b, c, d, e:platformer):
            return (test, a, b, c, d, e)

        mapping = perky.load("read_corpus/multiperky2.pky")
        got = app.read_mapping(test, mapping)

        expected = (
            test,
            "yes it's a",
            "oh my it's b",
            "\nc has a whole bunch of text!\nit's multi-line!\n   this line is indented!\n",
            ' I think d is quoted! ',
            [
                (
                    platformer,
                    'Hey Sparx!',
                    'Hey Spyro!',
                ),
                (
                    platformer,
                    'Hey Sparx go jump in a lake and drown!',
                    'Spyro I hate you!',
                )
            ]
        )
        self.assertEqual(expected, got)

    def test_perky_multioption_3(self):
        app = appeal.Appeal()

        class array_of_ints(appeal.MultiOption):
            def init(self, default):
                self.values = []
            def option(self, i:int):
                self.values.append((self.__class__, i))
            def render(self):
                return self.values

        class platformer(appeal.MultiOption):
            def init(self, default):
                self.values = []
            def option(self, ints:array_of_ints):
                self.values.append((self.__class__, ints))
            def render(self):
                return self.values


        def test(a, b, c, d, e:platformer):
            return (test, a, b, c, d, e)

        mapping = perky.load("read_corpus/multiperky3.pky")
        got = app.read_mapping(test, mapping)

        expected = (
            test,
            "yes it's a",
            "oh my it's b",
            "\nc has a whole bunch of text!\nit's multi-line!\n   this line is indented!\n",
            ' I think d is quoted! ',
            [
                (
                    platformer,
                    [
                        (array_of_ints, 1),
                        (array_of_ints, 2),
                    ]
                ),
                (
                    platformer,
                    [
                        (array_of_ints, 3),
                        (array_of_ints, 4),
                    ]
                ),
                (
                    platformer,
                    [
                        (array_of_ints, 5),
                        (array_of_ints, 6),
                    ]
                ),
            ]
        )
        self.assertEqual(expected, got)

    def test_toml_1(self):
        app = appeal.Appeal()

        def darth_vader(darth, vader):
            return (darth_vader, darth, vader)

        @app.unnested()
        def platformer(spyro, sparx):
            return (platformer, spyro, sparx)

        def test(a, b, c, d, e:darth_vader, f:platformer):
            return (test, a, b, c, d, e, f)

        with open("read_corpus/tomltest1.toml", "rt") as f:
            text = f.read()
        mapping = tomli.loads(text)

        got = app.read_mapping(test, mapping)

        expected = (
            test,
            "yes it's a",
            "oh my it's b",
            "c has a whole bunch of text!\nit's multi-line!\nthis line is indented!",
            3.14159,
            (
                darth_vader,
                'Join me',
                'And together we will rule the galaxy',
            ),
            (
                platformer,
                'Race you to Dragon Shores!',
                'Bzzzz, bzz-bzzzzz-bz-bz-BZZZZZ-bzz-bzz!',
            ),
        )

        self.assertEqual(expected, got)


    def test_toml_2(self):
        # what's the difference between test_toml_1 and test_toml_2?
        # test.d has an annotation of "float", which was a thing I had to handle.
        app = appeal.Appeal()

        def darth_vader(darth, vader):
            return (darth_vader, darth, vader)

        @app.unnested()
        def platformer(spyro, sparx):
            return (platformer, spyro, sparx)

        def test(a, b, c, d:float, e:darth_vader, f:platformer):
            return (test, a, b, c, d, e, f)

        with open("read_corpus/tomltest1.toml", "rt") as f:
            text = f.read()
        mapping = tomli.loads(text)

        got = app.read_mapping(test, mapping)

        expected = (
            test,
            "yes it's a",
            "oh my it's b",
            "c has a whole bunch of text!\nit's multi-line!\nthis line is indented!",
            3.14159,
            (
                darth_vader,
                'Join me',
                'And together we will rule the galaxy',
            ),
            (
                platformer,
                'Race you to Dragon Shores!',
                'Bzzzz, bzz-bzzzzz-bz-bz-BZZZZZ-bzz-bzz!',
            ),
        )

        self.assertEqual(expected, got)


# why not just annotate with big.parse_timestamp_3339Z?
# that takes a keyword argument, which appeal dutifully
# maps to an option.
def datestamp(date):
    return big.parse_timestamp_3339Z(date)

# go-faster stripe, saves up to 25% of runtime
_vote = appeal.validate_range(0, 5)
def vote(vote):
    return _vote(int(vote))

def nested_vote(q):
    return vote(q)

@dataclass
class AkaliSkinVote:
    voter_id: str
    vote_time: datestamp
    poll_id: str
    vote_Original: vote
    vote_Nurse: nested_vote
    vote_Blood_Moon: vote
    vote_Silverfang: vote
    vote_Headhunter: vote
    vote_Sashimi: vote
    vote_K_DA: vote
    vote_Project: vote
    vote_True_Damage: vote
    vote_K_DA_All_Out: vote
    vote_Crime_City_Nightmare: vote
    vote_Star_Guardian: vote
    vote_Stinger: vote
    vote_Infernal: vote
    vote_All_Star: vote
    vote_Prestige_K_DA: vote

def akali_skin_vote_fn(
    voter_id: str,
    vote_time: datestamp,
    poll_id: str,
    *votes: vote):
    return (AkaliSkinVote, voter_id, vote_time, poll_id, votes)


class TestReadCSV(unittest.TestCase):

    def setUp(self):
        with open("read_corpus/starvote_ballots_best_akali_skins.csv", "rt") as f:
            text = f.read().split("\n")
        self.csv_reader = csv.reader(text)

        self.row0 = AkaliSkinVote(
            voter_id='9szh13efc4',
            vote_time=datetime.datetime(2022, 8, 10, 9, 25, 51),
            poll_id='4tyx27ks',
            vote_Original=4,
            vote_Nurse=3,
            vote_Blood_Moon=3,
            vote_Silverfang=3,
            vote_Headhunter=2,
            vote_Sashimi=4,
            vote_K_DA=5,
            vote_Project=2,
            vote_True_Damage=3,
            vote_K_DA_All_Out=5,
            vote_Crime_City_Nightmare=4,
            vote_Star_Guardian=5,
            vote_Stinger=2,
            vote_Infernal=1,
            vote_All_Star=4,
            vote_Prestige_K_DA=5)

    def test_read_csv(self):
        app = appeal.Appeal()
        results = app.read_csv(AkaliSkinVote, self.csv_reader)
        self.assertEqual(len(results), 669)
        self.assertEqual(results[0], self.row0)

    def test_read_csv_var_positional(self):
        app = appeal.Appeal()
        results = app.read_csv(akali_skin_vote_fn, self.csv_reader)
        self.assertEqual(len(results), 669)
        row0 = akali_skin_vote_fn(
            '9szh13efc4',
            datetime.datetime(2022, 8, 10, 9, 25, 51),
            '4tyx27ks',
            4,
            3,
            3,
            3,
            2,
            4,
            5,
            2,
            3,
            5,
            4,
            5,
            2,
            1,
            4,
            5)

        self.assertEqual(results[0], row0)

    def test_read_csv_by_name(self):
        first_row_map = {
            'voterID': 'voter_id',
            'voteTime': 'vote_time',
            'pollID': 'poll_id',
            'Original': 'vote_Original',
            'Nurse': 'vote_Nurse',
            'Blood Moon': 'vote_Blood_Moon',
            'Silverfang': 'vote_Silverfang',
            'Headhunter': 'vote_Headhunter',
            'Sashimi': 'vote_Sashimi',
            'K/DA': 'vote_K_DA',
            'Project': 'vote_Project',
            'True Damage': 'vote_True_Damage',
            'K/DA All Out': 'vote_K_DA_All_Out',
            'Crime City Nightmare': 'vote_Crime_City_Nightmare',
            'Star Guardian': 'vote_Star_Guardian',
            'Stinger': 'vote_Stinger',
            'Infernal': 'vote_Infernal',
            'All-Star': 'vote_All_Star',
            'Prestige K/DA': 'vote_Prestige_K_DA',
        }
        app = appeal.Appeal()
        results = app.read_csv(AkaliSkinVote, self.csv_reader, first_row_map=first_row_map)
        self.assertEqual(len(results), 669)
        self.assertEqual(results[0], self.row0)

if __name__ == "__main__":
    unittest.main()
