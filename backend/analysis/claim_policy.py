"""Conservative lexical guard for AI consequences, not an entailment checker.

Known assertive outcomes need local modality; uncertainty elsewhere cannot
license them. Unknown paraphrases and quoted/source truth still need review.
"""
import re

INFERENCE_POLICY = (
    'Describe business effects only as uncertain scenarios. Use may/might/could '
    'or может/могут/возможен in each consequence clause; state that company '
    'exposure and magnitude are unknown unless independently established. '
    'Do not assert actual shortages, losses, cash gaps or guaranteed effects. '
    'Uncertainty in another sentence does not qualify a categorical claim.'
)
_CLAUSES = re.compile(r'[.!?;\n]+|,\s*|\b(?:и|а|но|однако|and|but|however)\b', re.IGNORECASE)
_OUTCOME = re.compile(
    r'\b(?:создаст\w*|создадут\w*|привед(?:ет|ут)|вызов(?:ет|ут)|'
    r'обеспеч(?:ит|ат)|гарантиру\w*|выраст(?:ет|ут)|возраст(?:ет|ут)|'
    r'увелич(?:ит|ат|ится|атся)|сниз(?:ит|ят|ится|ятся)|сократ(?:ит|ят|ится|ятся)|'
    r'возникн(?:ет|ут)|понес(?:ет|ут)|образова(?:лся|лась|лись)|получ(?:ит|ат)|'
    r'хватит|исчерпан\w*|will)\b')
_MODAL = re.compile(r'(?<!не )(?<!not )\b(?:может|могут|возможен|возможна|возможно|возможны|вероятно|may|might|could|possible|potential\w*)\b')
_ABSOLUTE = re.compile(r'\b(?:гарантирован\w*|неизбеж\w*|точно|definitely|guaranteed|certainly)\b')
_GUARANTEE = re.compile(r'(?<!не )(?<!not )\b(?:гарантирован\w*|неизбеж\w*|guaranteed|inevitable)\b')
_PRESENT_IMPACT = re.compile(r'\b(?:испытыва\w*|имеет|has|faces)\b.{0,100}\b(?:дефицит\w*|убыт\w*|shortage\w*|loss\w*|cash\s+gap)\b')


def unsupported_categorical_impact(text):
    text = text.lower().replace('ё', 'е')
    # Introductory modality belongs to the following predicate, not a separate
    # clause: "Возможно, ..." and "компания, вероятно, ..." remain uncertain.
    text = re.sub(r'\b(возможно|вероятно)\s*,', r'\1 ', text)
    for clause in _CLAUSES.split(text):
        if _GUARANTEE.search(clause):
            return True
        if (_OUTCOME.search(clause) or _PRESENT_IMPACT.search(clause)) and (_ABSOLUTE.search(clause) or not _MODAL.search(clause)):
            return True
    return False
