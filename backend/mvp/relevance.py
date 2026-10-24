"""Bounded source-clause relevance, independent of model output and case IDs.

Topic matches express editorial preference, never company exposure or causation.
"""
import re

SECTOR_TERMS = {
    'TRADE': ('торгов', 'оптов', 'рознич', 'ритейл'),
    'TRANSPORT': ('транспорт', 'логист', 'перевоз', 'железнодор', 'складирован'),
}
ROLE_TERMS = {
    'CFO': ('оплат', 'предоплат', 'платеж', 'аванс', 'дебитор', 'кредитор', 'денеж', 'поступлен', 'финанс', 'бюджет',
            'ликвид', 'оборотн', 'капитал', 'ставк', 'кредит', 'расчет', 'расчёт',
            'инфляц', 'марж', 'затрат', 'себестоим'),
    'COO': ('постав', 'достав', 'комплект', 'прием', 'приём', 'график', 'расписан',
            'слот', 'загруз', 'очеред', 'обработ', 'мощност', 'пропуск',
            'маршрут', 'логист', 'отгруз', 'упаков', 'сортиров', 'маркиров', 'сборк', 'рейс', 'смен'),
}


def _words(text):
    return re.findall(r'[а-яa-z]+', text.lower().replace('ё', 'е'))


def _hits(words, stems):
    # Whole word prefixes: an interest rate (ставка) is not a delivery (поставка).
    return sum(any(word.startswith(stem) for word in words)
               for stem in {stem.replace('ё', 'е') for stem in stems})


def _sectors(words):
    supported = {name for name, stems in SECTOR_TERMS.items() if _hits(words, stems)}
    # Warehouse operators/storage can identify logistics. A warehouse batch in
    # an explicitly trade clause describes an operation, not a second industry.
    warehouse = _hits(words, ('склад', 'хранен'))
    if warehouse and (not supported or _hits(words, ('оператор',))):
        supported.add('TRANSPORT')
    return supported


def source_relevance(text, category, role, activity_name=''):
    """Prefer role terms in the same clause as the supported profile category.

    Neutral macro news can match a role across sectors. A financial clause for
    another sector cannot outrank an operational clause by borrowing its sector.
    Ties retain repository order. Returned text is a source excerpt, not a fact.
    """
    generic = {'деят', 'торг', 'опто', 'розн', 'проч', 'тран', 'пере', 'услу'}
    activity_terms = {word[:4] for word in _words(activity_name)
                      if len(word) >= 4 and word[:4] not in generic}
    result = {'score': 0, 'excerpt': None, 'basis': 'no_supported_topic'}
    for clause in re.split(r'(?<=[.!?;])\s+|\n+', text or ''):
        clause = clause.strip()
        words = _words(clause)
        sectors = _sectors(words)
        own_hits, other_hits = category in sectors, bool(sectors - {category})
        role_hits = _hits(words, ROLE_TERMS.get(role, ()))
        if own_hits and not other_hits:
            activity_hits = _hits(words, activity_terms)
            score = 40 + 10 * min(role_hits, 3) + 20 * min(activity_hits, 2)
            basis = 'profile_category_and_role' if role_hits else 'profile_category_only'
        elif not own_hits and not other_hits and role_hits:
            score, basis = role_hits, 'cross_sector_role_topic'
        elif own_hits and other_hits and role_hits:
            score, basis = role_hits, 'mixed_sector_topic_uncertain'
        else:
            continue
        if score > result['score']:
            result = {'score': score, 'excerpt': clause[:350], 'basis': basis}
    return result
