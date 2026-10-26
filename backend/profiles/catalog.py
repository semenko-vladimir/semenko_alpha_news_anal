"""Versioned public profiles, separate from the anonymized CSV repository."""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .repository import CompanyProfile

SECTORS = {
    'ENERGY': 'Нефть и газ', 'MATERIALS': 'Металлы и материалы',
    'INDUSTRIALS': 'Промышленность и транспорт', 'CONSUMER_DISCRETIONARY': 'Товары длительного спроса',
    'CONSUMER_STAPLES': 'Товары повседневного спроса', 'HEALTH_CARE': 'Здравоохранение',
    'FINANCIALS': 'Финансы', 'INFORMATION_TECHNOLOGY': 'Информационные технологии',
    'COMMUNICATION_SERVICES': 'Связь', 'UTILITIES': 'Электроэнергетика', 'REAL_ESTATE': 'Недвижимость',
    'TRADE': 'Торговля', 'TRANSPORT': 'Транспорт и логистика',
}
OFFICIAL_HOSTS = {
    'PUB_LUKOIL': ('lukoil.ru','lukoil.com'), 'PUB_NORNICKEL': ('nornickel.ru',),
    'PUB_FESCO': ('fesco.ru','fesco.com'), 'PUB_MVIDEO': ('mvideoeldorado.ru',),
    'PUB_MAGNIT': ('magnit.com',), 'PUB_MDMEDICAL': ('mcclinics.ru',),
    'PUB_SBER': ('cbr.ru','sberbank.ru','sberbank.com'),
    'PUB_POSITIVE': ('ptsecurity.com',), 'PUB_MTS': ('mts.ru',),
    'PUB_INTERRAO': ('interrao.ru',), 'PUB_PIK': ('pik-group.ru','pik.ru'),
}


def official_url(company_id, value):
    if not isinstance(value, str) or len(value) > 1500:
        raise ValueError('public_source_url_invalid')
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    try:
        valid = parsed.scheme == 'https' and parsed.port in (None,443)
    except ValueError:
        valid = False
    if (not valid or parsed.username or parsed.password or
        not any(host == domain or host.endswith('.'+domain) for domain in OFFICIAL_HOSTS.get(company_id,()))):
        raise ValueError('public_source_not_official')
    return value


def _text(value, maximum=1500):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValueError('public_field_invalid')
    return value.strip()


def _date(value):
    if value is None: return None
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('public_date_timezone_required')
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def load_public_catalog(path):
    file = Path(path)
    if file.stat().st_size > 512_000: raise ValueError('public_catalog_too_large')
    data = json.loads(file.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('companies'),list) or len(data['companies']) != len(OFFICIAL_HOSTS):
        raise ValueError('public_catalog_schema_invalid')
    profiles, seen = [], set()
    for row in data['companies']:
        cid, sector = row.get('company_id'), row.get('sector_id')
        if cid not in OFFICIAL_HOSTS or cid in seen or sector not in SECTORS or sector in {'TRADE','TRANSPORT'}:
            raise ValueError('public_identity_or_sector_invalid')
        seen.add(cid)
        sources = []
        for index, source in enumerate(row.get('sources',[])):
            uri = official_url(cid,source.get('uri'))
            sources.append({'source_id':f'public:{cid}:{index}', 'kind':'company_profile',
                'title':_text(source.get('title'),300), 'uri':uri, 'locator':_text(source.get('locator'),300),
                'document_date':(_date(source.get('published_at')) or '')[:10] or None, 'published_at':_date(source.get('published_at')),
                'retrieved_at':_date(source.get('retrieved_at')), 'synthetic':False,
                'trust_status':'official_publication', 'text':_text(source.get('statement'),2000)})
        if not sources or len(sources)>5 or any(not s['retrieved_at'] for s in sources):
            raise ValueError('public_sources_required')
        metrics=[]
        for metric in row.get('metrics',[]):
            if (not isinstance(metric.get('value'),(int,float)) or isinstance(metric['value'],bool) or
                not math.isfinite(metric['value']) or not isinstance(metric.get('source_index'),int) or
                not 0 <= metric['source_index'] < len(sources)):
                raise ValueError('public_metric_invalid')
            metrics.append({'name':_text(metric.get('name'),120),'value':metric['value'],
                'unit':_text(metric.get('unit'),80),'currency':metric.get('currency'),
                'period':_text(metric.get('period'),40),'scope':_text(metric.get('scope'),40),
                'source_id':sources[metric['source_index']]['source_id']})
        annual_revenue = [m for m in metrics if m['name']=='Выручка' and m['currency']=='RUB' and
                          m['period'].isdigit() and len(m['period'])==4 and m['scope']=='group']
        eligibility='unknown'
        if annual_revenue:
            latest=max(annual_revenue,key=lambda m:m['period'])
            factor={'млрд ₽':1_000_000_000,'млн ₽':1_000_000,'₽':1}.get(latest['unit'])
            if factor: eligibility='confirmed' if latest['value']*factor>15_000_000_000 else 'below_threshold'
        features={'profile_origin':'public', 'sector_id':sector, 'sector_label':SECTORS[sector],
            'legal_name':_text(row.get('legal_name'),300), 'entity_scope':_text(row.get('entity_scope'),40),
            'activity_description':_text(row.get('activity'),500),'profile_as_of':row.get('as_of'),
            'profile_checked_at':max(s['retrieved_at'] for s in sources),'metrics':metrics,'eligibility':eligibility}
        version='sha256:'+hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        unknowns=['Публичный профиль не содержит банковские остатки, ставки и внутренние договоры.',
                  'Связь события с конкретной компанией требует проверки; отрасль сама по себе её не подтверждает.',
                  'Показатели относятся к указанному периоду и периметру отчётности.']
        if eligibility=='unknown': unknowns.append('Сопоставимая годовая выручка в рублях не установлена; порог 15 млрд ₽ не применён.')
        profiles.append(CompanyProfile(cid,sector,_text(row.get('display_name'),150),version,features,unknowns,sources))
    if len({p.category for p in profiles}) != len(OFFICIAL_HOSTS):raise ValueError('public_sectors_incomplete')
    return profiles


def profile_metadata(profile):
    public=profile.features.get('profile_origin')=='public'
    return {'company_id':profile.company_id,'display_name':profile.display_name,'category':profile.category,
        'profile_version':profile.version,'profile_origin':'public' if public else 'provided',
        'sector_id':profile.category,'sector_label':SECTORS.get(profile.category,'Отрасль не установлена'),
        'as_of':profile.features.get('profile_as_of'),'checked_at':profile.features.get('profile_checked_at'),
        'eligibility_status':profile.features.get('eligibility','unknown')}
