import dataclasses
import unittest

from alphacam.commands import UndoStack
from alphacam.model import FinishPass, Point

from .test_post import sample_project


class Undo(unittest.TestCase):
    def test_apply_undo_redo(self):
        p = sample_project("TWOOP_area_finish_metric")
        seen = []
        st = UndoStack(p, lambda proj: seen.append(len(proj.operations)))
        self.assertFalse(st.modified)
        st.apply("Feed", lambda q: setattr(q.operations[0], "feed", 0.2))
        self.assertEqual(p.operations[0].feed, 0.2)
        st.apply("Delete", lambda q: q.operations.pop(1))
        self.assertEqual(len(p.operations), 1)
        self.assertTrue(st.modified)
        self.assertEqual(st.undo(), "Delete")
        self.assertEqual(len(p.operations), 2)
        self.assertEqual(st.undo(), "Feed")
        self.assertEqual(p.operations[0].feed, 0.15)
        self.assertFalse(st.modified)
        self.assertEqual(st.redo(), "Feed")
        self.assertEqual(p.operations[0].feed, 0.2)
        self.assertEqual(seen, [2, 1, 2, 2, 2])

    def test_failed_edit_leaves_project_alone(self):
        p = sample_project("MET2_finish_metric")
        st = UndoStack(p)

        def bad(q):
            q.operations.append(FinishPass(3, Point(120, 5), ["g0"]))
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            st.apply("Bad", bad)
        self.assertEqual(len(p.operations), 1)
        self.assertFalse(st.can_undo)

    def test_no_op_edit_is_not_recorded(self):
        p = sample_project("MET2_finish_metric")
        st = UndoStack(p)
        st.apply("Same", lambda q: setattr(q.operations[0], "feed", 0.15))
        self.assertFalse(st.can_undo)

    def test_new_edit_clears_redo(self):
        p = sample_project("MET2_finish_metric")
        st = UndoStack(p)
        st.apply("A", lambda q: setattr(q, "program_number", 5))
        st.undo()
        st.apply("B", lambda q: setattr(q, "program_number", 6))
        self.assertFalse(st.can_redo)
        self.assertEqual(dataclasses.asdict(p.billet)["diameter"], 120.0)
