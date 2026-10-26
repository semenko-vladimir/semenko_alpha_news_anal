"""Bounded official HTML ingestion. Source text stays untrusted data."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit,urljoin
from urllib.request import Request,HTTPRedirectHandler,build_opener

from backend.profiles.catalog import OFFICIAL_HOSTS
from .cbr_rss import SourceItem

ROOTS=tuple(sorted({'mintrans.gov.ru','cbr.ru',*(d for hosts in OFFICIAL_HOSTS.values() for d in hosts)}))
MAX_BYTES=1_500_000


def official_url(value):
    if not isinstance(value,str) or len(value)>2048:raise ValueError('source_url_invalid')
    parsed=urlsplit(value);host=(parsed.hostname or '').lower().rstrip('.')
    try:valid=parsed.scheme=='https' and parsed.port in (None,443)
    except ValueError:valid=False
    if not valid or parsed.username or parsed.password or not any(host==d or host.endswith('.'+d) for d in ROOTS):
        raise ValueError('source_not_allowed')
    return value.split('#',1)[0]


class SafeRedirect(HTTPRedirectHandler):
    max_repeats=2
    max_redirections=3
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        official_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_official(url):
    url=official_url(url)
    request=Request(url,headers={'User-Agent':'AlphaHackathonResearch/1.0','Accept':'text/html,application/xhtml+xml'})
    with build_opener(SafeRedirect()).open(request,timeout=12) as response:
        official_url(response.url)
        kind=response.headers.get_content_type()
        if kind not in {'text/html','application/xhtml+xml'}:raise ValueError('source_not_html')
        data=response.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise ValueError('source_too_large')
        return data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta={};self.h1=[];self.title=[];self.paragraphs=[];self.links=[];self.all_text=[]
        self.skip=0;self.tags=[];self.href=None;self.link_text=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);tag=tag.lower();self.tags.append(tag)
        if tag in {'script','style','noscript'}:self.skip+=1
        if tag=='meta':self.meta[(attrs.get('property') or attrs.get('name') or '').lower()]=attrs.get('content','')
        if tag=='time' and attrs.get('datetime'):self.meta.setdefault('article:published_time',attrs['datetime'])
        if tag=='a':self.href=attrs.get('href');self.link_text=[]
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag=='a' and self.href:self.links.append((self.href,' '.join(self.link_text)));self.href=None
        if tag in {'script','style','noscript'}:self.skip=max(0,self.skip-1)
        if tag in self.tags:self.tags=self.tags[:len(self.tags)-1-self.tags[::-1].index(tag)]
    def handle_data(self,text):
        if self.skip:return
        text=' '.join(text.split())
        if not text:return
        self.all_text.append(text)
        if 'h1' in self.tags:self.h1.append(text)
        if 'title' in self.tags:self.title.append(text)
        if 'p' in self.tags:self.paragraphs.append(text)
        if self.href:self.link_text.append(text)


def _publication(document):
    value=document.meta.get('article:published_time') or document.meta.get('date') or document.meta.get('datepublished')
    if value:
        dt=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        if dt.tzinfo is None:
            # A publication day is known; no clock time is inferred from retrieval.
            if len(value.strip())!=10:raise ValueError('publication_timezone_unknown')
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    text=' '.join(document.all_text)
    months={'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,'июля':7,'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12}
    match=re.search(r'опубликовано\s*:?\s*(\d{1,2})\s+('+'|'.join(months)+r')\s+(20\d{2})',text,re.I)
    if match:return datetime(int(match[3]),months[match[2].lower()],int(match[1]),tzinfo=timezone.utc).isoformat().replace('+00:00','Z')
    return None


def parse_article(html,url):
    url=official_url(url)
    if not isinstance(html,str) or len(html)>MAX_BYTES:raise ValueError('source_too_large')
    doc=Document();doc.feed(html);doc.close()
    title=doc.meta.get('og:title') or ' '.join(doc.h1) or ' '.join(doc.title)
    title=' '.join(title.split())[:1000]
    if len(title)<5:raise ValueError('source_title_missing')
    text=doc.meta.get('og:description') or doc.meta.get('description') or ' '.join(doc.paragraphs[:2]) or title
    text=' '.join(text.split())[:500]
    published=_publication(doc)
    if published and datetime.fromisoformat(published.replace('Z','+00:00'))>datetime.now(timezone.utc)+timedelta(days=1):
        raise ValueError('publication_in_future')
    payload='\0'.join((title,text,published or ''))
    return SourceItem('official:'+sha256(url.encode()).hexdigest(),title,url,url,published,published,text,
                      sha256(payload.encode()).hexdigest(),provenance='official_publication',synthetic=False)


def refresh_source(repo,source,fetch=fetch_official):
    url=official_url(source['url']);source_id=source['id']
    fetched=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    with repo._connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS news_official_refresh (
            source_id TEXT PRIMARY KEY,source_url TEXT NOT NULL,last_attempt_at TEXT NOT NULL,
            status TEXT NOT NULL,error TEXT,last_success_at TEXT)''')
        previous=conn.execute('SELECT last_success_at FROM news_official_refresh WHERE source_id=?',(source_id,)).fetchone()
    last_success=previous[0] if previous else None
    try:
        html=fetch(url);doc=Document();doc.feed(html)
        pattern=re.compile(source['pattern'])
        candidates=[]
        for href,label in doc.links:
            target=urljoin(url,href)
            try:target=official_url(target)
            except ValueError:continue
            if target!=url and pattern.search(urlsplit(target).path) and len(label.strip())>=12 and target not in candidates:
                candidates.append(target)
        candidates=candidates[:min(3,max(1,int(source.get('article_limit',2))))]
        if not candidates:raise ValueError('source_no_article_links')
        items=[parse_article(fetch(target),target) for target in candidates]
        result=repo.ingest(items,retrieved_at=fetched)
        status,error,last_success='ok',None,fetched
    except Exception as exc:
        known={'source_no_article_links','source_not_html','source_too_large','publication_timezone_unknown','source_title_missing','source_not_allowed'}
        error=str(exc) if str(exc) in known else 'invalid_source_data' if isinstance(exc,ValueError) else ('http_'+str(exc.code) if hasattr(exc,'code') else type(exc).__name__)
        status='error';result={'inserted':0,'received':0}
    with repo._connect() as conn:
        conn.execute('INSERT OR REPLACE INTO news_official_refresh VALUES(?,?,?,?,?,?)',(source_id,url,fetched,status,error,last_success))
    return {'source_id':source_id,'status':status,'error':error,'last_attempt_at':fetched,'last_success_at':last_success,
            'inserted':result['inserted'],'received':result['received']}
