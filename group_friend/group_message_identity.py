"""Bubble-local native group sender metadata; no nickname or geometric identity fallback."""

GROUP_SENDERS = r'''elements=>{
 const out=[];
 for(const box of elements){
  let el=box.querySelector('[data-e2e="msg-item-content"]')||box;
  for(let depth=0;depth<7&&el;depth++,el=el.parentElement){
   const pk=Object.keys(el).find(k=>k.startsWith('__reactProps'));
   const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));
   const ps=pk?[el[pk]]:[];let f=fk&&el[fk];
   for(let n=0;n<5&&f;n++,f=f.return)ps.push(f.memoizedProps);
   let found=false;
   for(const p of ps){const m=p?.message||p?.msg||p?.item?.message;if(!m)continue;
    const id=String(m.serverId||m.server_id||m.messageId||m.message_id||m.id||'');
    const mine=typeof m.isMyMessage==='boolean'?m.isMyMessage:typeof m.isFromMe==='boolean'?m.isFromMe:null;
    out.push({message_id:id,group_id:String(m.conversationId||''),sender_id:String(m.sender||m.senderId||''),mine});
    found=true;break;
   }
   if(found||el===box)break;
  }
 }return out;
}'''

PRIVATE_BINDING = r'''el=>{
 const pk=Object.keys(el).find(k=>k.startsWith('__reactProps'));
 const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));
 const ps=pk?[el[pk]]:[];let f=fk&&el[fk];
 for(let n=0;n<8&&f;n++,f=f.return)ps.push(f.memoizedProps);
 const found=new Map();for(const p of ps){const c=p?.conversation||p?.curConversation;if(!c)continue;
  const sec=String(c.toParticipantSecUserId||c._toParticipantSecUserId||'');
  const uid=String(c.toParticipantUserId||'');if(sec&&uid)found.set(sec,{sec_uid:sec,user_id:uid,type:Number(c.type),
   owner_user_id:String(c.__internal_ctx?.option?.userId||'')});
 }return found.size===1?[...found.values()][0]:null;
}'''
