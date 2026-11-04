"""Verify the formal assistant message that owns a report's editing work."""
import hashlib
import json
import re

from markdown_it import MarkdownIt
from management_portal.home_report import isa_reader, normalize_target, digest

RESIDENT = re.compile(r'resident_[a-f0-9]{12}')


def verified_source(actor, context, origin_key, target, *, read=None):
    if context is None:
        # An authenticated Resident can own its own directly authored report.
        if RESIDENT.fullmatch(actor or ''):
            return {'status':'verified','resident_id':actor,'kind':'authenticated_resident'}
        return {'status':'unassigned','error':'缺少正式作者消息关联，需指定负责修改的Resident。'}
    if not isinstance(context,dict) or set(context) != {'resident_id','message_id','fence_index'}:
        raise ValueError('报告作者来源必须是Resident、消息ID与展示围栏序号')
    resident, message_id, index = (context[k] for k in ('resident_id','message_id','fence_index'))
    if not isinstance(resident,str) or not RESIDENT.fullmatch(resident) or not isinstance(message_id,str) or not re.fullmatch(r'msg_[a-f0-9]{12,64}',message_id):
        raise ValueError('无效报告作者消息地址')
    if isinstance(index,bool) or not isinstance(index,int) or not 0 <= index < 1000:
        raise ValueError('无效报告展示围栏序号')
    result=(read or isa_reader(actor))('GetMessage',{'resident_id':resident,'message_id':message_id})
    message=result.get('message') or result
    if message.get('role') not in {'assistant','agent'} or (message.get('message_id') or message.get('id')) != message_id:
        raise ValueError('报告作者必须来自真实assistant消息')
    text='\n'.join(str(part.get('text') or '') for part in message.get('content_parts',[]) if part.get('type')=='text')
    if len(text)>1024*1024:
        raise ValueError('报告作者消息超出读取范围')
    # Both current Host paths increment only after the exact protocol-language
    # check. Ordinary code does not consume this persisted message ordinal.
    blocks=[token for token in MarkdownIt('commonmark').parse(text) if token.type=='fence' and token.info.strip()=='portal-home-display']
    if index >= len(blocks):
        raise ValueError('原消息不存在该展示指令')
    block=blocks[index]
    closing=text.splitlines()[block.map[1]-1]
    if len(block.content)>8192 or not re.fullmatch(r' {0,3}'+re.escape(block.markup[0])+'{'+str(len(block.markup))+r',}[ \t]*',closing):
        raise ValueError('展示指令必须是完整围栏')
    value=json.loads(block.content)
    if not isinstance(value,dict) or value.get('protocol')!='portal-home-display/v1' or value.get('view')!='report' or value.get('action','open') not in {'open','generate','focus'}:
        raise ValueError('原消息不是正式报告展示指令')
    if not isinstance(value.get('request_id'),str) or not value['request_id'].strip() or len(value['request_id'])>200 or re.search(r'[\x00-\x1f]',value['request_id']):
        raise ValueError('展示请求身份无效')
    if isinstance(value.get('version'),bool) or not isinstance(value.get('version'),int) or value['version']<1:
        raise ValueError('展示指令版本无效')
    if 'quality' in value and value['quality'] not in {'low','medium','high'}:
        raise ValueError('展示指令质量无效')
    raw=value.get('target') or {}
    if not isinstance(raw,dict):
        raise ValueError('展示目标无效')
    normalized=normalize_target({'kind':raw.get('kind'),'id':str(raw.get('id') or '').strip()})
    if isinstance(raw.get('focus'),str):
        # JS slice counts UTF-16 code units, including an unmatched final half.
        normalized['focus']=raw['focus'].encode('utf-16-le','surrogatepass')[:400].decode('utf-16-le','surrogatepass')
    if normalize_target(normalized) != normalize_target(target):
        raise ValueError('展示指令与报告来源不一致')
    # Match Home's stable, quality-independent JSON identity and key order.
    intent={'action':value.get('action','open'),'view':'report','target':normalized}
    # JSON.stringify escapes lone surrogates but preserves other Unicode.
    expected='home-message/v1:'+hashlib.sha256(json.dumps([resident,message_id,index,intent],ensure_ascii=False,separators=(',',':')).encode('utf-8','backslashreplace')).hexdigest()
    if origin_key != expected:
        raise ValueError('展示请求与正式消息身份不一致')
    # The conversation's formal assistant produced the display instruction;
    # do not infer an unrelated AMA or busy member as its author.
    return {'status':'verified','resident_id':resident,'kind':'assistant_message',
            'source':dict(context),'message_sha256':digest(text),'intent':intent}


def recheck(actor, binding, origin_key, target, *, read=None):
    if not binding or binding.get('status')!='verified':
        return binding
    if binding.get('kind')=='authenticated_resident':
        return binding
    current=verified_source(actor,binding.get('source'),origin_key,target,read=read)
    if current != binding:
        raise ValueError('报告作者来源已改变，不能继续原修改任务')
    return current
