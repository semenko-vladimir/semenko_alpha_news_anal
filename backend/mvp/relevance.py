"""Bounded source-clause relevance, independent of model output and case IDs.

Topic matches express editorial preference, never company exposure or causation.
"""
import re

SECTOR_TERMS = {
    'TRADE': ('торгов', 'оптов', 'рознич', 'ритейл'),
    'TRANSPORT': ('транспорт', 'логист', 'перевоз', 'железнодор', 'складирован'),
    'ENERGY': ('нефт', 'углеводород', 'газодобы', 'нефтепродукт'),
    'MATERIALS': ('металлург', 'металл', 'руд', 'никел', 'медн', 'стал', 'удобрен'),
    'INDUSTRIALS': ('контейнер', 'промышлен', 'машиностро', 'портов', 'судоход'),
    'CONSUMER_DISCRETIONARY': ('электроник', 'бытов', 'непродовольствен', 'автомобил'),
    'CONSUMER_STAPLES': ('продовольствен', 'пищев', 'агропромышлен'),
    'HEALTH_CARE': ('клиник', 'медицин', 'госпитал', 'фармацевт', 'лекарств'),
    'FINANCIALS': ('банковск', 'банкомат', 'страхов', 'депозит'),
    'INFORMATION_TECHNOLOGY': ('кибербезопас', 'программно', 'разработчик'),
    'COMMUNICATION_SERVICES': ('телеком', 'сотов', 'мобильн', 'широкополос'),
    'UTILITIES': ('электроэнерг', 'генераци', 'теплоснабж', 'энергоснабж', 'энергосбыт'),
    'REAL_ESTATE': ('девелоп', 'строитель', 'недвижим', 'жилищн'),
}
ROLE_TERMS = {
    'CFO': ('оплат', 'предоплат', 'платеж', 'аванс', 'дебитор', 'кредитор', 'денеж', 'поступлен', 'финанс', 'бюджет',
            'ликвид', 'оборотн', 'капитал', 'ставк', 'кредит', 'расчет', 'расчёт',
            'инфляц', 'марж', 'затрат', 'себестоим'),
    'COO': ('постав', 'достав', 'комплект', 'прием', 'приём', 'график', 'расписан',
            'слот', 'загруз', 'очеред', 'обработ', 'мощност', 'пропуск',
            'маршрут', 'логист', 'отгруз', 'упаков', 'сортиров', 'маркиров', 'сборк', 'рейс', 'смен',
            'внедрен', 'внедрени', 'мощност', 'обработ', 'доступност'),
}


def _words(text):
    return re.findall(r'[а-яa-z]+', text.lower().replace('ё', 'е'))


def _hits(words, stems):
    # Whole word prefixes: an interest rate (ставка) is not a delivery (поставка).
    return sum(any(word.startswith(stem) for word in words)
               for stem in {stem.replace('ё', 'е') for stem in stems})


def _sectors(words, category=None):
    # Preserve the two-sector legacy rubric; broader public profiles use the full taxonomy.
    terms = {k:v for k,v in SECTOR_TERMS.items() if k in {'TRADE','TRANSPORT'}} if category in {'TRADE','TRANSPORT'} else SECTOR_TERMS
    supported = {name for name, stems in terms.items() if _hits(words, stems)}
    if category not in {'TRADE','TRANSPORT'} and _hits(words,('информационн',)) and _hits(words,('безопасност',)):
        supported.add('INFORMATION_TECHNOLOGY')
    if _hits(words,('инсайдер','бирж','трейдер')):
        supported.discard('TRADE')
    # Warehouse operators/storage can identify logistics. A warehouse batch in
    # an explicitly trade clause describes an operation, not a second industry.
    warehouse = _hits(words, ('склад', 'хранен'))
    if warehouse and (not supported or _hits(words, ('оператор',))):
        supported.add('TRANSPORT')
    if category == 'INDUSTRIALS' and 'TRANSPORT' in supported:
        supported.discard('TRANSPORT'); supported.add('INDUSTRIALS')
    if category in {'CONSUMER_DISCRETIONARY','CONSUMER_STAPLES'} and 'TRADE' in supported:
        supported.discard('TRADE'); supported.add(category)
    return supported


def source_relevance(text, category, role, activity_name='', *, strict=False):
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
        sectors = _sectors(words, category)
        own_hits, other_hits = category in sectors, bool(sectors - {category})
        role_hits = _hits(words, ROLE_TERMS.get(role, ()))
        if strict:
            meaningful={'CFO':('ставк','кредит','финансирован','оборотн','платеж','оплат','дебитор','кредитор','ликвид','денежн','марж','себестоим','бюджет','налог','расход','инвестиц','выручк'),
                        'COO':('постав','достав','отгруз','график','мощност','внедрен','перевоз','ремонт','обработк','подключен','рейс','смен')}
            role_hits=_hits(words,meaningful.get(role,()))
            if not role_hits and _hits(words,('поздрав','наград','праздник','преми','социальн')):continue
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
