"""Locally maintained analysis templates; public excerpts are not official case forms."""
import json
from datetime import date
from uuid import uuid4
from typing import Literal
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from .database import Base
from .knowledge import _session, _WRITE_LOCK, _audit

class AnalysisTemplate(Base):
    __tablename__ = 'analysis_templates'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[str] = mapped_column(Text)

class Basis(BaseModel):
    kind: Literal["qualification", "penalty", "discretion"] = "qualification"
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    article: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=6000)
    source: str = Field(min_length=1, max_length=1000)
    effective_from: date
    effective_to: date | None = None
    @field_validator('source')
    @classmethod
    def safe_url(cls, value):
        parsed = urlparse(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username:
            raise ValueError('来源须为公开 HTTP(S) 链接')
        return value
    @model_validator(mode='after')
    def dates(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError('截止日期不能早于生效日期')
        return self

class TemplateBody(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    checklist: str = Field(min_length=1, max_length=3000)
    enabled: bool = True
    revision: int = Field(default=1, ge=1)
    bases: list[Basis] = Field(min_length=1, max_length=10)

router = APIRouter(prefix='/api/knowledge/analysis/templates', tags=['分析模板'])

@router.get('')
def templates(request: Request):
    with _session(request) as session:
        return {'items': [{'id': row.id, **json.loads(row.data)} for row in session.scalars(select(AnalysisTemplate).order_by(AnalysisTemplate.id))]}

@router.post('')
def create_template(request: Request, body: TemplateBody):
    with _WRITE_LOCK, _session(request, write=True) as session:
        row = AnalysisTemplate(id=str(uuid4()), data=body.model_copy(update={'revision': 1}).model_dump_json())
        session.add(row)
        _audit(session, 'analysis_template.created', row.id)
        return {'id': row.id, **json.loads(row.data)}

@router.put('/{template_id}')
def edit_template(request: Request, template_id: str, body: TemplateBody):
    with _WRITE_LOCK, _session(request, write=True) as session:
        row = session.get(AnalysisTemplate, template_id)
        if not row:
            raise HTTPException(404, '模板不存在')
        if json.loads(row.data)['revision'] != body.revision:
            raise HTTPException(409, '模板已更新，请关闭并重新打开后编辑。')
        row.data = body.model_copy(update={'revision': body.revision + 1}).model_dump_json()
        _audit(session, 'analysis_template.updated', row.id)
        return {'id': row.id, **json.loads(row.data)}

def resolve_template(session, template_id, revision, as_of):
    row = session.get(AnalysisTemplate, template_id)
    if not row:
        raise HTTPException(404, '分析模板不存在')
    template = TemplateBody.model_validate_json(row.data)
    if not template.enabled:
        raise HTTPException(409, '分析模板已停用，请重新选择。')
    if revision != template.revision:
        raise HTTPException(409, '分析模板已更新，请刷新页面后重新分析。')
    sources = []
    for i, basis in enumerate(template.bases):
        if basis.effective_from <= as_of and (not basis.effective_to or as_of <= basis.effective_to):
            sources.append({'id': f'template-{template_id}-{template.revision}-{i}', 'document_id': '', 'template_source': True, 'version': f'模板依据 · 修订 {template.revision}', 'page': None, 'score': 1, 'allow_external': True, **basis.model_dump(mode='json')})
    if not sources:
        raise HTTPException(409, '该日期没有有效模板依据，请管理员补充适用版本。')
    return template, sources

def analysis_question(question, template, as_of):
    return json.dumps({'任务': '依据模板分析用户陈述，输出辅助分析，不作处罚决定', '模板': template.name, '适用日期': str(as_of), '核查清单': template.checklist, '事实陈述（未核实）': question, '输出要求': '仍仅输出claims数组；按【初步定性】【要件对照】【待核实事项】三部分组织，处罚及裁量原文由系统补充，每项text以对应标题开头并引用真实source_ids。逐项对照行为、主体、时间和证据，明确符合、不符合或待核实。不得把用户陈述当作已查证事实。未提供的事实不得臆测，特别不能默认初次违法、严重程度或合法手续不存在。事实与所选模板不符时明确不适用。区分法律罚则和裁量基准，给出条件和幅度，不选定罚款额或拘留天数。引用只能证明规范，不能证明事实。缺少适用依据、例外规则或事实则明确待核实；不得机械按天罚款。'}, ensure_ascii=False)

LAW = 'https://www.nia.gov.cn/n794014/n1050181/n1050479/c1013311/content.html'
RULE = 'https://www.nia.gov.cn/n794014/n1050181/n1050479/c1014161/content.html'
DISCRETION = 'https://www.nia.gov.cn/n741440/n741587/n1316094/n1459245/c1593719/content.html'
def basis(title, article, text, source=LAW, start='2013-07-01'):
    return dict(title=title, article=article, text=text, source=source, effective_from=start, kind='discretion' if '裁量基准' in title else 'penalty' if article in ('第七十八条', '第八十条第一款') else 'qualification')

def seed_analysis_templates(engine):
    law = '中华人民共和国出境入境管理法'
    criteria = '违反《中华人民共和国出境入境管理法》行为的处罚裁量基准'
    boundary = basis(criteria, '第三十条、第三十二条、第三十四条', '第三十条 本基准规定的“以上”、“以下”包括本数，但对分段连续记数中的重叠数字，“以上”不含本数。第三十二条 个别案件确实不宜适用裁量基准的，可突破裁量基准依法作出处罚决定，但应当有充分的理由，履行严格的审批手续后方可实施。第三十四条 本基准自2017年9月1日起实施。', DISCRETION, '2017-09-01')
    defaults = [dict(id='builtin-illegal-stay', name='涉嫌非法居留', description='核查停留居留期限、合法手续与行为类型，匹配定性和处罚依据。依据公开资料编制的辅助分析模板，并非官方文书。', checklist='主体及年龄；入境方式；证件允许停留期限（不是签证入境有效期）；实际停留区间及证据；延期申请和受理回执；是否免签、临时入境或区域限制；是否再次违法及证据；特殊身份或例外；从轻减轻、不予处罚等情形需补充依据。', bases=[
        basis('中华人民共和国外国人入境出境管理条例', '第二十五条', '外国人在中国境内有下列情形之一的，属于非法居留：（一）超过签证、停留居留证件规定的停留居留期限停留居留的；（二）免办签证入境的外国人超过免签期限停留且未办理停留居留证件的；（三）外国人超出限定的停留居留区域活动的；（四）其他非法居留的情形。', RULE, '2013-09-01'),
        basis(law, '第七十八条', '外国人非法居留的，给予警告；情节严重的，处每非法居留一日五百元，总额不超过一万元的罚款或者五日以上十五日以下拘留。因监护人或者其他负有监护责任的人未尽到监护义务，致使未满十六周岁的外国人非法居留的，对监护人或者其他负有监护责任的人给予警告，可以并处一千元以下罚款。'),
        basis(criteria, '第十四条', '非法居留的，根据《出境入境管理法》第七十八条第一款的规定，按照以下标准给予处罚：（一）超过签证、停留居留证件规定的停留居留期限非法居留十日以下的，处警告。（二）具有以下情形之一的，处每非法居留一日五百元，总额不超过一万元的罚款或者五日拘留：1.超过签证、停留居留证件规定的停留居留期限非法居留十日以上六十日以下的（罚款从非法居留第十一日起计算）；2.超过临时入境手续规定的期限停留、超过免签期限停留且未办理停留居留证件、超出限定的停留居留区域活动等非法居留二十日以下的；3.再次非法居留二十日以下的。（三）具有以下情形之一的，处一万元罚款或者五日以上十五日以下拘留：1.超过签证、停留居留证件规定的停留居留期限非法居留六十日以上的；2.超过临时入境手续规定的期限停留、超过免签期限停留且未办理停留居留证件、超出限定的停留居留区域活动等非法居留二十日以上的；3.再次非法居留二十日以上的；4.其他情节严重的。', DISCRETION, '2017-09-01'), boundary]),
      dict(id='builtin-illegal-work', name='涉嫌非法就业', description='核查实际工作、许可范围及就业期限，区分外国人本人和用工主体责任。依据公开资料编制的辅助分析模板，并非官方文书。', checklist='主体及年龄；是否永久居留等特殊身份；实际工作行为与证据；工作许可和工作类居留证件；许可单位、地域与实际是否一致；留学生勤工助学岗位和时限；工作起止日期；所得金额及证据；一年内同类处罚；是否仅为聘用方或介绍方；从轻减轻、不予处罚等情形需补充依据。', bases=[
        basis(law, '第四十三条、第四十八条（节选）', '第四十三条 外国人有下列行为之一的，属于非法就业：（一）未按照规定取得工作许可和工作类居留证件在中国境内工作的；（二）超出工作许可限定范围在中国境内工作的；（三）外国留学生违反勤工助学管理规定，超出规定的岗位范围或者时限在中国境内工作的。第四十八条 取得永久居留资格的外国人，凭永久居留证件在中国境内居留和工作。'),
        basis(law, '第八十条第一款', '外国人非法就业的，处五千元以上二万元以下罚款；情节严重的，处五日以上十五日以下拘留，并处五千元以上二万元以下罚款。'),
        basis(criteria, '第十七条', '非法就业的，根据《出境入境管理法》第八十条第一款的规定，按照以下标准给予处罚：（一）外国人非法就业三十日以下，处五千元以上一万元以下的罚款。（二）外国人非法就业三十日以上九十日以下，处一万元以上二万元以下的罚款。（三）具有以下情形之一的，处五日以上十日以下拘留，并处五千元以上一万元以下的罚款：1.外国人非法就业九十日以上一年以下的；2.非法就业所得三万元以上五万元以下的。（四）具有以下情形之一的，处十日以上十五日以下拘留，并处一万元以上二万元以下罚款：1.外国人非法就业一年以上的；2.非法就业所得五万元以上的；3.因非法就业被处罚后一年内再次实施相同违法行为的；4.其他情节严重的。', DISCRETION, '2017-09-01'), boundary])]
    with Session(engine) as session, session.begin():
        for item in defaults:
            template_id = item.pop('id')
            existing = session.get(AnalysisTemplate, template_id)
            if existing is not None:
                data = json.loads(existing.data)
                kinds = {b['text']: b['kind'] for b in item['bases']}
                for entry in data['bases']:
                    if 'kind' not in entry:
                        entry['kind'] = kinds.get(entry['text'], 'qualification')
                existing.data = json.dumps(data, ensure_ascii=False)
            else:
                session.add(AnalysisTemplate(id=template_id, data=TemplateBody(**item).model_dump_json()))
