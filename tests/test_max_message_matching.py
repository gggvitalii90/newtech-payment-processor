from payment_processor.max_message_matching import MessageEvidence, match_signatures, match_signatures_by_sequence


def signature(message_id: str, seq: int, timestamp: float = 0, author_id: str = "") -> MessageEvidence:
    return MessageEvidence(
        message_id=message_id,
        seq=seq,
        timestamp=timestamp or seq * 60,
        author_id=author_id,
        signature={"object_name": message_id},
    )


def file(message_id: str, seq: int, timestamp: float = 0, author_id: str = "") -> MessageEvidence:
    return MessageEvidence(
        message_id=message_id,
        seq=seq,
        timestamp=timestamp or seq * 60,
        author_id=author_id,
        file_ids=(message_id,),
    )


def test_signature_in_same_body_is_exact() -> None:
    evidence = MessageEvidence(
        message_id="m1",
        seq=10,
        timestamp=600,
        signature={"object_name": "ПСК"},
        file_ids=("f1",),
    )

    match = match_signatures([evidence])["f1"]

    assert match.signature_message_id == "m1"
    assert match.confidence == "exact_body"


def test_unique_signature_before_file_is_matched() -> None:
    match = match_signatures([signature("s1", 10), file("f1", 11)])["f1"]

    assert match.signature_message_id == "s1"
    assert match.confidence == "unique_pair"


def test_unique_signature_after_file_is_matched() -> None:
    match = match_signatures([file("f1", 10), signature("s1", 11)])["f1"]

    assert match.signature_message_id == "s1"
    assert match.confidence == "unique_pair"


def test_reply_signature_is_exact() -> None:
    reply = MessageEvidence(
        message_id="s1",
        seq=20,
        timestamp=1200,
        signature={"object_name": "ПСК"},
        linked_message_ids=("file-message",),
    )
    target = MessageEvidence(
        message_id="file-message",
        seq=10,
        timestamp=600,
        file_ids=("f1",),
    )

    match = match_signatures([target, reply])["f1"]

    assert match.signature_message_id == "s1"
    assert match.confidence == "exact_link"


def test_two_files_and_two_signatures_are_ambiguous_without_evidence() -> None:
    matches = match_signatures(
        [
            signature("s1", 10),
            signature("s2", 11),
            file("f1", 12),
            file("f2", 13),
        ]
    )

    assert matches["f1"].confidence == "ambiguous"
    assert matches["f2"].confidence == "ambiguous"
    assert matches["f1"].signature == {}
    assert matches["f2"].signature == {}


def test_signature_outside_window_is_not_matched() -> None:
    matches = match_signatures(
        [signature("s1", 1, timestamp=1), file("f1", 2, timestamp=2000)],
        window_seconds=1800,
    )

    assert matches["f1"].confidence == "ambiguous"
    assert matches["f1"].signature_message_id == ""


def test_sequence_matching_resolves_signature_file_chain_from_right_edge() -> None:
    evidence = [
        signature("s1", 10),
        file("f1", 11),
        signature("s2", 12),
        file("f2", 13),
    ]

    matches = match_signatures_by_sequence(evidence)

    assert matches["f1"].signature_message_id == "s1"
    assert matches["f2"].signature_message_id == "s2"
    assert matches["f1"].confidence == "sequence_unique"


def test_sequence_matching_resolves_file_signature_chain_from_left_edge() -> None:
    evidence = [
        file("f1", 10),
        signature("s1", 11),
        file("f2", 12),
        signature("s2", 13),
    ]

    matches = match_signatures_by_sequence(evidence)

    assert matches["f1"].signature_message_id == "s1"
    assert matches["f2"].signature_message_id == "s2"


def test_sequence_matching_keeps_two_files_competing_for_one_signature_ambiguous() -> None:
    evidence = [file("f1", 10), signature("s1", 11), file("f2", 12)]

    matches = match_signatures_by_sequence(evidence)

    assert matches["f1"].confidence == "ambiguous"
    assert matches["f2"].confidence == "ambiguous"


def test_sequence_matching_assigns_preceding_unsigned_block_from_same_author() -> None:
    evidence = [
        file("a1", 10, author_id="author-a"),
        file("a2", 11, author_id="author-a"),
        file("b1", 12, author_id="author-b"),
        signature("sa", 13, author_id="author-a"),
        signature("sb", 14, author_id="author-b"),
    ]

    matches = match_signatures_by_sequence(evidence)

    assert matches["a1"].signature_message_id == "sa"
    assert matches["a2"].signature_message_id == "sa"
    assert matches["a1"].confidence == "author_block"
    assert matches["b1"].signature_message_id == "sb"


def test_sequence_matching_does_not_mix_authors() -> None:
    evidence = [
        file("a1", 10, author_id="author-a"),
        file("b1", 11, author_id="author-b"),
        signature("sa", 12, author_id="author-a"),
    ]

    matches = match_signatures_by_sequence(evidence)

    assert matches["a1"].signature_message_id == "sa"
    assert matches["b1"].confidence == "ambiguous"


def test_sequence_matching_uses_document_fields_to_choose_between_sides() -> None:
    before = MessageEvidence(
        message_id="s1",
        seq=10,
        timestamp=600,
        signature={"counterparty": "ООО Альфа", "object_name": "ПСК"},
    )
    target = file("f1", 11)
    after = MessageEvidence(
        message_id="s2",
        seq=12,
        timestamp=720,
        signature={"counterparty": "ООО Бета", "object_name": "ПР"},
    )

    matches = match_signatures_by_sequence(
        [before, target, after],
        {"f1": {"counterparty": "ООО Бета"}},
    )

    assert matches["f1"].signature_message_id == "s2"
    assert matches["f1"].confidence == "document_match"
