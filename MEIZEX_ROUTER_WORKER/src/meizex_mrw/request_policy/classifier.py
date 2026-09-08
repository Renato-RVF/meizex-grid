"""Pure, deterministic request classifier. No LLM, no embeddings, no network.

Ported from MOL (meizex_orchestrator_lite/request_policy/classifier.py),
unchanged except the import path. See models.py for why this was
ported instead of extending MRW's own task_classifier.py.

Precedence (checked in order, first match wins): ARITHMETIC, TEXT_COUNTING,
DATE_COMPUTATION, UNIT_CONVERSION, SEQUENCE_ANALYSIS, BOOLEAN_LOGIC,
CODE_TRACE, FILESYSTEM, CURRENT_STATE, CREATIVE_GENERATION,
TEXT_TRANSFORMATION, else UNKNOWN. TEXT_COUNTING added 2026-09-05
(Achado 56, not from MOL) for the same reason ARITHMETIC exists:
"quantas letras tem X" has exactly one correct answer, computable
deterministically -- a real stress test caught a small local LLM
answering it wrong. DATE_COMPUTATION and UNIT_CONVERSION added the same
day (Achado 57, not from MOL) for the same reason: date arithmetic and
unit conversion both have exactly one mechanically correct answer.
SEQUENCE_ANALYSIS and BOOLEAN_LOGIC added the same day (Achado 58, not
from MOL): sorting/min/max/next-term and AND/OR/NOT/XOR evaluation are
equally mechanical. CODE_TRACE added the same day (Achado 73, category S
of the expanded questionnaire): "what does this code print" has exactly
one correct answer, computable by actually tracing it.
FILESYSTEM is checked before CURRENT_STATE so a request like "o arquivo
mais recente da pasta Downloads" classifies as FILESYSTEM (the concrete,
actionable capability) rather than the more generic CURRENT_STATE.

NO MATCH -> UNKNOWN. Never UNKNOWN -> MODEL_ALLOWED automatically; that
mapping lives in policy.py, not here.

Also includes:
  - natural-language arithmetic detection (an allow-listed intent prefix
    plus a real embedded expression - not merely "2+2" appearing anywhere,
    which would wrongly fire on "Leia o arquivo 2+2.txt.");
  - FILESYSTEM operation/effect sub-classification (classify_filesystem_operation),
    which stops every FILESYSTEM request from defaulting to read-only
    regardless of what was actually asked.
"""

from __future__ import annotations

import re

from meizex_mrw.request_policy.models import PolicyReasonCode, RequestClass

_ARITHMETIC_PATTERN = re.compile(r"^[0-9\s.+\-*/×÷()]+\??$")

# A real embedded binary expression - number, operator, number - not a
# bare substring match. Bounded to the same operator set as the strict
# pure-arithmetic pattern above ('^' included for exponent word problems,
# e.g. "quanto é 2^10?" -- deterministic_arithmetic.py translates it to
# Python's ** before evaluating, never Python's own '^' XOR operator).
# '×'/'÷' (common Portuguese multiplication/division signs, distinct from
# ASCII '*'/'/') added 2026-09-05 (Achado 72, category R): without them,
# "17 × 19 = 321. Confirme." fell through to UNKNOWN and reached the LLM
# unprotected against a false premise embedded in the prompt.
_ARITHMETIC_EXPRESSION_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*[+\-*/×÷^]\s*\d+(?:\.\d+)?")

# An explicit equation claim, e.g. "17 × 19 = 321", COMBINED with a
# confirmation-intent word ("confirme", "certeza", "correto") -- category
# R, Achado 72: "Tenho quase certeza de que 17 × 19 = 321. Confirme." has
# no "quanto é"/"calcule" prefix, so without this it fell through to
# UNKNOWN and reached the LLM unprotected against the embedded false
# premise. Deliberately requires BOTH the equation AND a confirmation
# word -- an equation alone (e.g. "2 + 2 = 4?" inside a yes/no framing
# like "Responda apenas com SIM: ...") is NOT enough evidence on its own:
# that is a meta-question about the equation, not a request to compute
# it, and a real regression (test_worker_format_constraint.py) confirmed
# a bare-equation trigger wrongly intercepts it before the format-
# constraint check ever runs.
_ARITHMETIC_EQUATION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*[+\-*/×÷^]\s*\d+(?:\.\d+)?\s*=\s*\d+(?:\.\d+)?"
)
_CONFIRMATION_INTENT_WORDS = (
    "confirme", "confirma", "certeza", "correto", "está certo", "esta certo",
)

# Word-problem phrasings deterministic_arithmetic.py's _compute_word_problem
# cascade recognizes (percentage-of, discount, splitting money, average,
# remainder) -- unlike the intent PREFIXES above, these substrings are
# specific enough on their own (no plain digit-operator-digit expression
# is embedded) to signal ARITHMETIC without also requiring one of
# _ARITHMETIC_INTENT_PREFIXES.
_ARITHMETIC_WORD_PROBLEM_PATTERNS = (
    "% de",
    "desconto de",
    "divida",
    "divide",
    "média de",
    "media de",
    "resto da divisão",
    "resto da divisao",
)

# Allow-listed arithmetic-intent phrases (PT+EN). A number+operator+number
# substring alone is not enough - "Leia o arquivo 2+2.txt." must not
# become ARITHMETIC just because "2+2" appears in a filename.
_ARITHMETIC_INTENT_PREFIXES = (
    "quanto é", "quanto e", "calcule", "calcular", "calculate",
    "resultado de", "solve", "resolver", "compute",
)

# Counting-intent phrases matching deterministic_counting.py's extractors
# (letter/word/char/vowel/consonant counts, string reversal). Deliberately
# narrow phrase matches, not a general NLU signal -- the same discipline
# as _ARITHMETIC_INTENT_PREFIXES above.
_TEXT_COUNTING_INTENT_PATTERNS = (
    "quantas letras",
    "quantas vezes",
    "quantas palavras",
    "quantos caracteres",
    "quantas vogais",
    "quantas consoantes",
    "inverta a string",
    "inverta exatamente a string",
)

# Date/unit intent phrases matching deterministic_dates_units.py's
# extractors. Deliberately narrow phrase matches, not a general NLU
# signal -- the same discipline as _ARITHMETIC_INTENT_PREFIXES above.
_DATE_COMPUTATION_INTENT_PATTERNS = (
    "quantos dias há entre", "quantos dias ha entre",
    "que dia da semana",
    "que data é", "que data e", "que data fica",
)
_UNIT_CONVERSION_INTENT_PATTERNS = (
    "quantos km", "quantas km",
    "quantas milhas", "quantos milhas",
    "quantos kg", "quantas kg",
    "quantas libras", "quantos libras",
    "quantos metros", "quantas metros",
    "quantos pés", "quantos pes", "quantas pés", "quantas pes",
    "quantos litros", "quantas litros",
    "quantos galões", "quantos galoes", "quantas galões", "quantas galoes",
    "quantos graus fahrenheit", "quantos graus celsius",
)

# Sequence-analysis and boolean-logic intent phrases matching
# deterministic_sequences_logic.py's extractors. Deliberately narrow
# phrase matches, not a general NLU signal.
_SEQUENCE_ANALYSIS_INTENT_PATTERNS = (
    "ordene os números", "ordene os numeros",
    "ordene números", "ordene numeros",
    "qual o maior número", "qual o maior numero",
    "qual é o maior número", "qual e o maior numero",
    "qual o menor número", "qual o menor numero",
    "qual é o menor número", "qual e o menor numero",
    "próximo número na sequência", "proximo numero na sequencia",
    "qual é maior", "qual e maior",
    "quantos números há na lista", "quantos numeros ha na lista",
)
# "e"/"ou" alone are far too common in Portuguese to be a signal -- this
# class only fires when "verdadeiro"/"falso" appear together with a
# connector or negation, never on those connector words alone.
_BOOLEAN_LOGIC_CONNECTOR_WORDS = ("e", "ou", "não", "nao", "xor", "exclusivo")


def _looks_like_boolean_logic(text: str) -> bool:
    text_lower = text.lower()
    has_bool_literal = "verdadeiro" in text_lower or "falso" in text_lower
    if not has_bool_literal:
        return False
    words = _words(text)
    return bool(words & set(_BOOLEAN_LOGIC_CONNECTOR_WORDS))


_FILESYSTEM_KEYWORDS = (
    "arquivo", "arquivos", "pasta", "pastas", "diretório", "diretorio",
    "downloads", "file", "files", "folder", "folders", "directory",
)

# A bare Windows path or a dotted filename (e.g. "C:\temp\a.txt") is a
# filesystem reference even without any of the keywords above - needed so
# "Crie C:\temp\a.txt." classifies as FILESYSTEM rather than falling
# through to UNKNOWN for lack of the word "arquivo".
_FILE_PATH_PATTERN = re.compile(
    r"[A-Za-z]:\\|\b\w+\.(?:txt|csv|md|pdf|docx?|json|log|py|xlsx?)\b", re.IGNORECASE
)

_CURRENT_STATE_KEYWORDS = (
    "mais recente", "agora", "atual", "current", "now", "latest",
    "processo em execução", "status do sistema",
)

_CREATIVE_KEYWORDS = (
    "poema", "poem", "conte uma história", "conte uma historia",
    "write a story", "escreva um", "escreva uma", "compose a",
)

_TEXT_TRANSFORMATION_KEYWORDS = (
    "traduza", "translate", "resuma", "summarize", "resumo",
)

# --- FILESYSTEM operation/effect sub-signals -------------------------------

_LIST_TOKENS = frozenset({"list", "listar", "liste"})
_READ_DOC_TOKENS = frozenset({"read", "ler", "leia", "conteúdo", "conteudo", "content",
                               "mostrar", "show", "inspecionar", "inspect",
                               "consulte", "consultar", "verifique", "verificar"})
_LATEST_TOKENS = frozenset({"latest", "recente"})  # "mais recente" tokenizes to "mais","recente"

_CREATE_TOKENS = frozenset({"create", "criar", "crie"})
_WRITE_TOKENS = frozenset({"write", "escrever", "escreva", "append", "adicionar", "edit", "editar"})
_DELETE_TOKENS = frozenset({"delete", "apagar", "apague", "remove", "remover"})
_RENAME_TOKENS = frozenset({"rename", "renomear"})
_MOVE_TOKENS = frozenset({"move", "mover"})
_COPY_TOKENS = frozenset({"copy", "copiar"})

# Noun cues that disambiguate a bare "create" verb into FILE vs DIRECTORY.
# FILE != DIRECTORY: "crie arquivo.txt" is a file-create intent, "crie uma
# pasta" is a directory-create intent; a bare "crie" with no noun cannot be
# confidently classified and is left ambiguous (never guessed).
_DIR_NOUN_TOKENS = frozenset(
    {"pasta", "pastas", "diretório", "diretorio", "folder", "folders", "directory", "directories"}
)
_FILE_NOUN_TOKENS = frozenset({"arquivo", "arquivos", "file", "files"})
# A dotted filename/extension is strong evidence of a FILE (not directory)
# intent - e.g. "Crie C:\\temp\\a.txt." is a file-create, never ambiguous.
_FILE_EXT_TOKENS = frozenset({"txt", "csv", "md", "pdf", "docx", "doc",
                            "json", "log", "py", "xlsx", "xls"})

# CREATE is handled separately (it needs file/directory disambiguation), so
# it is intentionally absent from this table.
_MUTATE_TOKEN_GROUPS: tuple[tuple[frozenset, PolicyReasonCode], ...] = (
    (_DELETE_TOKENS, PolicyReasonCode.FILESYSTEM_DELETE_DETECTED),
    (_RENAME_TOKENS, PolicyReasonCode.FILESYSTEM_RENAME_DETECTED),
    (_MOVE_TOKENS, PolicyReasonCode.FILESYSTEM_MOVE_DETECTED),
    (_COPY_TOKENS, PolicyReasonCode.FILESYSTEM_COPY_DETECTED),
    (_WRITE_TOKENS, PolicyReasonCode.FILESYSTEM_WRITE_DETECTED),
)

_WORD_PATTERN = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_PATTERN.finditer(text)}


def _looks_arithmetic(text: str) -> bool:
    stripped = text.strip()
    if not stripped or not _ARITHMETIC_PATTERN.match(stripped):
        return False
    has_digit = any(ch.isdigit() for ch in stripped)
    has_operator = any(op in stripped for op in "+-*/×÷")
    return has_digit and has_operator


def _looks_arithmetic_natural_language(text: str) -> bool:
    text_lower = text.lower()
    if _contains_any(text_lower, _ARITHMETIC_WORD_PROBLEM_PATTERNS):
        return True
    has_confirmation_intent = _contains_any(text_lower, _CONFIRMATION_INTENT_WORDS)
    if has_confirmation_intent and _ARITHMETIC_EQUATION_PATTERN.search(text):
        return True
    if not any(prefix in text_lower for prefix in _ARITHMETIC_INTENT_PREFIXES):
        return False
    return bool(_ARITHMETIC_EXPRESSION_PATTERN.search(text))


def _contains_any(text_lower: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text_lower for keyword in keywords)


def _looks_like_counting(text: str) -> bool:
    return _contains_any(text.lower(), _TEXT_COUNTING_INTENT_PATTERNS)


def _looks_like_date_computation(text: str) -> bool:
    return _contains_any(text.lower(), _DATE_COMPUTATION_INTENT_PATTERNS)


def _looks_like_unit_conversion(text: str) -> bool:
    return _contains_any(text.lower(), _UNIT_CONVERSION_INTENT_PATTERNS)


def _looks_like_sequence_analysis(text: str) -> bool:
    return _contains_any(text.lower(), _SEQUENCE_ANALYSIS_INTENT_PATTERNS)


def _looks_like_code_trace(text: str) -> bool:
    return "código:" in text.lower() or "codigo:" in text.lower()


def classify_request_class(text: str) -> tuple[RequestClass, tuple[PolicyReasonCode, ...]]:
    """Pure function: same text always yields the same (class, reasons)."""
    if text is None:
        return RequestClass.UNKNOWN, (PolicyReasonCode.NO_DETERMINISTIC_RULE,)

    # MULTI-CAPABILITY REQUEST != PRECEDENCE WINNER: a request that needs
    # BOTH an arithmetic capability and a filesystem capability cannot be
    # honestly served by a single deterministic class. Fail safe to
    # REQUIRES_REFINEMENT rather than silently dropping one half.
    _is_arithmetic = _looks_arithmetic(text) or _looks_arithmetic_natural_language(text)
    _is_filesystem = _contains_any(text.lower(), _FILESYSTEM_KEYWORDS) or bool(
        _FILE_PATH_PATTERN.search(text)
    )
    if _is_arithmetic and _is_filesystem:
        return RequestClass.UNKNOWN, (PolicyReasonCode.CLARIFICATION_NEEDED,)

    if _looks_arithmetic(text):
        return RequestClass.ARITHMETIC, (PolicyReasonCode.ARITHMETIC_DETECTED,)

    if _looks_arithmetic_natural_language(text):
        return RequestClass.ARITHMETIC, (
            PolicyReasonCode.ARITHMETIC_DETECTED,
            PolicyReasonCode.ARITHMETIC_NATURAL_LANGUAGE_DETECTED,
        )

    if _looks_like_counting(text):
        return RequestClass.TEXT_COUNTING, (PolicyReasonCode.TEXT_COUNTING_DETECTED,)

    if _looks_like_date_computation(text):
        return RequestClass.DATE_COMPUTATION, (PolicyReasonCode.DATE_COMPUTATION_DETECTED,)

    if _looks_like_unit_conversion(text):
        return RequestClass.UNIT_CONVERSION, (PolicyReasonCode.UNIT_CONVERSION_DETECTED,)

    if _looks_like_sequence_analysis(text):
        return RequestClass.SEQUENCE_ANALYSIS, (PolicyReasonCode.SEQUENCE_ANALYSIS_DETECTED,)

    if _looks_like_boolean_logic(text):
        return RequestClass.BOOLEAN_LOGIC, (PolicyReasonCode.BOOLEAN_LOGIC_DETECTED,)

    if _looks_like_code_trace(text):
        return RequestClass.CODE_TRACE, (PolicyReasonCode.CODE_TRACE_DETECTED,)

    text_lower = text.lower()

    if _contains_any(text_lower, _FILESYSTEM_KEYWORDS) or _FILE_PATH_PATTERN.search(text):
        return RequestClass.FILESYSTEM, (PolicyReasonCode.FILESYSTEM_REFERENCE_DETECTED,)

    if _contains_any(text_lower, _CURRENT_STATE_KEYWORDS):
        return RequestClass.CURRENT_STATE, (PolicyReasonCode.CURRENT_STATE_REFERENCE_DETECTED,)

    if _contains_any(text_lower, _CREATIVE_KEYWORDS):
        return RequestClass.CREATIVE_GENERATION, (PolicyReasonCode.MODEL_USE_PERMITTED,)

    if _contains_any(text_lower, _TEXT_TRANSFORMATION_KEYWORDS):
        return RequestClass.TEXT_TRANSFORMATION, (PolicyReasonCode.MODEL_USE_PERMITTED,)

    return RequestClass.UNKNOWN, (PolicyReasonCode.NO_DETERMINISTIC_RULE,)


def classify_filesystem_operation(text: str) -> tuple[PolicyReasonCode, ...]:
    """Bounded, deterministic FILESYSTEM operation/effect sub-classification.

    Returns one or more reason codes describing what specific filesystem
    operation(s) the text signals - never a guess when signals conflict
    or are absent (MULTI-ACTION REQUEST != SINGLE TOOL REQUIREMENT;
    MUTATING REQUEST != READ_ONLY REQUIREMENT).
    """
    words = _words(text)

    has_list = bool(words & _LIST_TOKENS)
    has_read_doc = bool(words & _READ_DOC_TOKENS)
    has_latest = bool(words & _LATEST_TOKENS)
    has_dir_noun = bool(words & _DIR_NOUN_TOKENS)
    has_file_noun = bool(words & _FILE_NOUN_TOKENS) or bool(words & _FILE_EXT_TOKENS)
    has_create_verb = bool(words & _CREATE_TOKENS)

    # CREATE needs FILE vs DIRECTORY disambiguation (FILE != DIRECTORY). A
    # bare "create" with no noun cannot be confidently classified and is left
    # ambiguous (never guessed) - REQUIRES_REFINEMENT, not a wrong capability.
    create_kind = None
    if has_create_verb:
        if has_dir_noun and not has_file_noun:
            create_kind = "DIR"
        elif has_file_noun and not has_dir_noun:
            create_kind = "FILE"

    mutate_matches = [
        reason for tokens, reason in _MUTATE_TOKEN_GROUPS if words & tokens
    ]
    if create_kind == "DIR":
        mutate_matches.append(PolicyReasonCode.FILESYSTEM_DIRECTORY_CREATE_DETECTED)
    elif create_kind == "FILE":
        mutate_matches.append(PolicyReasonCode.FILESYSTEM_CREATE_DETECTED)
    has_mutate = bool(mutate_matches)

    # Directory nouns imply an observation context ONLY when no create verb is
    # present - "Crie uma pasta" is a directory CREATE, not list+create.
    has_dir_observation = has_list or (has_dir_noun and not has_create_verb)
    has_read_signal = has_dir_observation or has_read_doc

    if has_read_signal and has_mutate:
        return (PolicyReasonCode.FILESYSTEM_MIXED_SIGNAL_DETECTED,)

    if has_mutate:
        if len(mutate_matches) > 1:
            return (PolicyReasonCode.FILESYSTEM_MULTIPLE_MUTATION_KINDS_DETECTED, *mutate_matches)
        return (PolicyReasonCode.FILESYSTEM_MUTATION_DETECTED, mutate_matches[0])

    if has_list and has_read_doc:
        return (PolicyReasonCode.FILESYSTEM_COMPOUND_LATEST_CONTENT_DETECTED,)

    if (has_latest and has_read_doc) or (has_latest and has_dir_observation):
        return (PolicyReasonCode.FILESYSTEM_COMPOUND_LATEST_CONTENT_DETECTED,)

    if has_list:
        return (PolicyReasonCode.FILESYSTEM_DIRECTORY_OBSERVATION_DETECTED,)

    if has_read_doc:
        return (PolicyReasonCode.FILESYSTEM_DOCUMENT_READ_DETECTED,)

    return (PolicyReasonCode.FILESYSTEM_NO_OPERATION_SIGNAL_DETECTED,)
