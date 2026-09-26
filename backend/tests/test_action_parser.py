"""Unit tests for the NLP ActionParser."""
from __future__ import annotations

import json
from unittest.mock import patch
import pytest

from app.actions.parser import ActionParser
from app.actions.types import AddClassAction, CancelClassAction, SwapClassesAction


def test_parse_single_add_class():
    parser = ActionParser()
    with patch("app.actions.parser.call_groq") as mock_call:
        mock_call.return_value = (json.dumps([{
            "action": "ADD_CLASS",
            "spec": {
                "program": "B.Tech",
                "semester": "5th",
                "day": "Friday",
                "start_time": "14:30",
                "end_time": "17:30",
                "subject_code": "dbms-p",
                "subject_name": "DBMS Practical",
                "teacher": "SC",
                "entry_type": "Practical",
                "room": "R#205"
            }
        }]), "mock_model")

        actions = parser.parse("Add DBMS practical...")
        assert len(actions) == 1
        assert isinstance(actions[0], AddClassAction)
        assert actions[0].spec.room == "R#205"
        assert actions[0].spec.teacher == "SC"


def test_parse_multi_action_complex():
    parser = ActionParser()
    with patch("app.actions.parser.call_groq") as mock_call:
        mock_call.return_value = (json.dumps([
            {
                "action": "CANCEL_CLASS",
                "target": {"day": "Tuesday", "teacher": "SK"}
            },
            {
                "action": "SWAP_CLASSES",
                "target_a": {"day": "Monday", "program": "M.Tech", "semester": "1st"},
                "target_b": {"day": "Tuesday", "program": "M.Tech", "semester": "1st"}
            }
        ]), "mock_model")

        actions = parser.parse("Cancel SK Tuesday class and swap M.Tech Mon/Tue")
        assert len(actions) == 2
        assert isinstance(actions[0], CancelClassAction)
        assert isinstance(actions[1], SwapClassesAction)
        assert actions[1].target_a.day == "Monday"


def test_parse_invalid_llm_output_self_repair():
    """Verify that a malformed JSON first response triggers a self-repair attempt."""
    parser = ActionParser()
    
    # Provide an iterator for side_effect to return different values on consecutive calls
    responses = iter([
        ("Not a json array, just some text", "mock_model"), # 1st call fails
        (json.dumps([{"action": "CANCEL_CLASS", "target": {"day": "Monday"}}]), "mock_model") # 2nd call succeeds
    ])
    
    with patch("app.actions.parser.call_groq") as mock_call:
        mock_call.side_effect = lambda *args, **kwargs: next(responses)
        
        actions = parser.parse("cancel monday class")
        assert len(actions) == 1
        assert isinstance(actions[0], CancelClassAction)
        assert mock_call.call_count == 2


def test_parse_fails_after_max_attempts():
    parser = ActionParser()
    with patch("app.actions.parser.call_groq") as mock_call:
        mock_call.return_value = ("Still garbage", "mock_model")
        
        with pytest.raises(ValueError, match="Failed to parse NLP command"):
            parser.parse("hello")
        
        assert mock_call.call_count == 2  # initial + 1 repair limit
