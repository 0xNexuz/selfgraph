from __future__ import annotations

import logging
import re

from .._marks import MARK_CLASS
from ..errors import LLM_HINT
from ..models import EntityInput, Extraction, InsertWarning, ParsedDocument
from .extractor import _FALLBACK_TYPE, LLMExtractor, _RelOut, _WarningSink

_log = logging.getLogger("selfgraph")

__all__ = ["NERExtractor"]

_SPACY = None
_SPACY_READY = False
_SPACY_HAS_LIB = False
_TYPE_MAP = {
    "PERSON": "Person",
    "ORG": "Organization",
    "GPE": "Place",
    "LOC": "Place",
    "PRODUCT": "Product",
    "EVENT": "Event",
    "WORK_OF_ART": "Work",
    "FAC": "Facility",
    "NORP": "Group",
    "LAW": "Law",
    "LANGUAGE": "Language",
}
_WORD = rf"[\w&.'\-{MARK_CLASS}]"
_UPPERCASE = (
    "41-5A C0-D6 D8-DE 100-136/2 139-147/2 14A-178/2 179-17D/2 181-182 184-186/2 187-189/2 18A-18B 18E-191 "
    "193-194 196-198 19C-19D 19F-1A0 1A2-1A6/2 1A7-1A9/2 1AC-1AE/2 1AF-1B1/2 1B2-1B3 1B5-1B7/2 1B8 1BC 1C4 "
    "1C7 1CA 1CD-1DB/2 1DE-1EE/2 1F1 1F4-1F6/2 1F7-1F8 1FA-232/2 23A-23B 23D-23E 241-243/2 244-246 248-24E/2 "
    "370-372/2 376 37F 386-388/2 389-38A 38C-38E/2 38F-391/2 392-3A1 3A3-3AB 3CF 3D2-3D4 3D8-3EE/2 3F4 "
    "3F7-3F9/2 3FA 3FD-42F 460-480/2 48A-4C0/2 4C1-4CD/2 4D0-52E/2 531-556 10A0-10C5 10C7 10CD 13A0-13F5 "
    "1C90-1CBA 1CBD-1CBF 1E00-1E94/2 1E9E-1EFE/2 1F08-1F0F 1F18-1F1D 1F28-1F2F 1F38-1F3F 1F48-1F4D "
    "1F59-1F5F/2 1F68-1F6F 1FB8-1FBB 1FC8-1FCB 1FD8-1FDB 1FE8-1FEC 1FF8-1FFB 2102 2107 210B-210D 2110-2112 "
    "2115 2119-211D 2124-212A/2 212B-212D 2130-2133 213E-213F 2145 2160-216F 2183 24B6-24CF 2C00-2C2F "
    "2C60-2C62/2 2C63-2C64 2C67-2C6D/2 2C6E-2C70 2C72 2C75 2C7E-2C80 2C82-2CE2/2 2CEB-2CED/2 2CF2 A640-A66C/2 "
    "A680-A69A/2 A722-A72E/2 A732-A76E/2 A779-A77D/2 A77E-A786/2 A78B-A78D/2 A790-A792/2 A796-A7AA/2 "
    "A7AB-A7AE A7B0-A7B4 A7B6-A7C4/2 A7C5-A7C7 A7C9 A7D0 A7D6-A7D8/2 A7F5 FF21-FF3A 10400-10427 104B0-104D3 "
    "10570-1057A 1057C-1058A 1058C-10592 10594-10595 10C80-10CB2 118A0-118BF 16E40-16E5F 1D400-1D419 "
    "1D434-1D44D 1D468-1D481 1D49C-1D49E/2 1D49F 1D4A2 1D4A5-1D4A6 1D4A9-1D4AC 1D4AE-1D4B5 1D4D0-1D4E9 "
    "1D504-1D505 1D507-1D50A 1D50D-1D514 1D516-1D51C 1D538-1D539 1D53B-1D53E 1D540-1D544 1D546 1D54A-1D550 "
    "1D56C-1D585 1D5A0-1D5B9 1D5D4-1D5ED 1D608-1D621 1D63C-1D655 1D670-1D689 1D6A8-1D6C0 1D6E2-1D6FA "
    "1D71C-1D734 1D756-1D76E 1D790-1D7A8 1D7CA 1E900-1E921 1F130-1F149 1F150-1F169 1F170-1F189"
)


def _expand_uppercase(runs: str) -> str:
    """Expand Unicode 15.0 uppercase code point runs.

    @param runs (str): space-separated hex code points or spans with optional steps.
    @return (str): the letters, in code point order.
    """
    letters: list[str] = []
    for run in runs.split():
        span, _, step = run.partition("/")
        first, _, last = span.partition("-")
        letters.extend(map(chr, range(int(first, 16), int(last or first, 16) + 1, int(step or 1))))
    return "".join(letters)


_UPPERCASE_LETTERS = _expand_uppercase(_UPPERCASE)
_UPPER = f"[{re.escape(_UPPERCASE_LETTERS)}]"
_CAP = re.compile(rf"{_UPPER}{_WORD}*(?:\s+{_UPPER}{_WORD}*)*")
_SENT = re.compile(r"(?<=[.!?])\s+")
_STOPNAMES = frozenset(
    {
        "the",
        "a",
        "an",
        "he",
        "she",
        "it",
        "they",
        "we",
        "you",
        "i",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "his",
        "her",
        "their",
        "our",
        "your",
        "its",
    }
)


def _nlp():
    global _SPACY, _SPACY_READY, _SPACY_HAS_LIB
    if not _SPACY_READY:
        _SPACY_READY = True
        try:
            import spacy
        except ImportError:  # pragma: no cover
            return _SPACY
        _SPACY_HAS_LIB = True
        try:
            _SPACY = spacy.load("en_core_web_sm")
        except OSError:  # pragma: no cover
            _SPACY = None
    return _SPACY


def _spacy_extract(nlp, text: str):
    doc = nlp(text)
    entities = [EntityInput(type=_TYPE_MAP.get(e.label_, _FALLBACK_TYPE), name=e.text) for e in doc.ents]
    tok_ent = {i: e for e in doc.ents for i in range(e.start, e.end)}

    def ent_for(token):
        for node in token.subtree:
            if node.i in tok_ent:
                return tok_ent[node.i]
        return None

    relations = []
    for tok in doc:
        if tok.pos_ != "VERB":
            continue
        subjects = [c for c in tok.children if c.dep_ in ("nsubj", "nsubjpass")]
        objects = [c for c in tok.children if c.dep_ in ("dobj", "attr", "dative", "oprd")]
        for prep in (c for c in tok.children if c.dep_ == "prep"):
            objects += [c for c in prep.children if c.dep_ == "pobj"]
        for subj in subjects:
            se = ent_for(subj)
            if se is None:
                continue
            for obj in objects:
                oe = ent_for(obj)
                if oe is not None and oe.text != se.text:
                    relations.append(_RelOut(from_name=se.text, type=tok.lemma_.upper(), to_name=oe.text))
    return entities, relations


def _regex_extract(text: str):
    entities, relations = [], []
    for sentence in _SENT.split(text):
        found = (m.group(0) for line in sentence.splitlines() for m in _CAP.finditer(line))
        raw = (" ".join(n.split()).strip(".,;:!?") for n in found)
        names = list(dict.fromkeys(n for n in raw if n and n.lower() not in _STOPNAMES))
        entities.extend(EntityInput(type=_FALLBACK_TYPE, name=name) for name in names)
        relations.extend(
            _RelOut(from_name=a, type="RELATED_TO", to_name=b) for a, b in zip(names, names[1:], strict=False)
        )
    return entities, relations


class NERExtractor(_WarningSink):
    def __init__(self, engine: str = "spacy") -> None:
        """An LLM-free extractor: spaCy NER when asked for and installed, else a regex pass.

        @param engine (str): "spacy" to use spaCy when it and a model are installed, or "regex" to never load it.
        """
        self._warnings: list[InsertWarning] = []
        self._engine = engine
        self.tier = "regex"

    async def extract(self, document: ParsedDocument) -> Extraction:
        nlp = _nlp() if self._engine == "spacy" else None
        code = "basic_extraction"
        self.tier = "regex" if nlp is None else "spacy"
        if nlp is not None:
            entities, relations = _spacy_extract(nlp, document.text)
            code, engine = "ner_extraction", "spaCy NER"
        elif self._engine != "spacy":
            entities, relations = _regex_extract(document.text)
            engine = "heuristic regex"
        else:
            entities, relations = _regex_extract(document.text)
            if _SPACY_HAS_LIB:
                engine = "heuristic regex; run 'python -m spacy download en_core_web_sm' for spaCy NER"
            else:
                engine = "heuristic regex; install the 'ner' extra for spaCy NER"
        message = f"extracted without an LLM using {engine}; for model-based extraction, {LLM_HINT}"
        _log.warning("%s: %s", code, message)
        self._warnings = [InsertWarning(code=code, message=message)]
        return LLMExtractor._assemble(entities, relations, [], [], self._warnings)
