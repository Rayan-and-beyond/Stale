from stale.agents._slide_ref_verifier import expand_short_claims


def test_expands_truncated_sentence_claim_from_extractor_candidate():
    findings = [{"claim": "Finalize is used to perform"}]
    candidates = {
        "candidates": [
            {
                "snippet": (
                    "Difference between final, finally and finalize\n"
                    "...\n"
                    "Finalize is used to perform clean up processing just "
                    "before object is garbage collected.\n"
                    "...\n"
                    "Finalize is a method."
                )
            }
        ]
    }

    summary = expand_short_claims(findings, candidates)

    assert findings[0]["claim"] == (
        "Finalize is used to perform clean up processing just before object "
        "is garbage collected."
    )
    assert summary["expanded"] == 1


def test_does_not_expand_code_fragment_claims():
    findings = [{"claim": 'executeQuery ("SELECT * FROM AUTHORS WHERE FIRSTNAME LIKE'}]
    candidates = {
        "candidates": [
            {
                "snippet": (
                    'executeQuery ("SELECT * FROM AUTHORS WHERE FIRSTNAME LIKE \'%" '
                    '+ filterText.getText() + "%\'")'
                )
            }
        ]
    }

    summary = expand_short_claims(findings, candidates)

    assert findings[0]["claim"] == (
        'executeQuery ("SELECT * FROM AUTHORS WHERE FIRSTNAME LIKE'
    )
    assert summary["expanded"] == 0
