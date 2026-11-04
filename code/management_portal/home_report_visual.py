"""Continuous Portal-style frames from a source-mapped reasoning plan."""
import math

BG, TEXT, BLUE, PURPLE, GREEN, AMBER = '#f7f9fc', '#17243b', '#4263c7', '#835cc4', '#238467', '#ad661d'


def frame(plan, step, elapsed, duration, caption, fonts, trace_fraction=None):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1280, 720), BG); draw = ImageDraw.Draw(image)
    def text(x, y, value, size=22, width=1150, lines=2, fill=TEXT):
        line, rendered = '', []
        for char in str(value):
            if draw.textlength(line + char, font=fonts[size]) > width:
                rendered.append(line); line = ''
            line += char
        rendered.append(line)
        for i, line in enumerate(rendered[:lines]):
            draw.text((x, y+i*(size+5)), line, font=fonts[size], fill=fill)
    def arrow(a, b, color, fraction=1, width=3):
        end=(a[0]+(b[0]-a[0])*fraction, a[1]+(b[1]-a[1])*fraction)
        draw.line((*a,*end),fill=color,width=width)
        if fraction >= .99:
            angle=math.atan2(b[1]-a[1], b[0]-a[0]); size=9
            draw.polygon([end,(end[0]-size*math.cos(angle-.5),end[1]-size*math.sin(angle-.5)),(end[0]-size*math.cos(angle+.5),end[1]-size*math.sin(angle+.5))],fill=color)
    text(32,22,'TFI · '+plan['steps'][step]['title'],32)
    text(32,70,plan['question'],22)
    dependency = plan['mode'] == 'dependency'
    text(32,108,'本地结构演示 · 原始任务状态不变' if dependency else '关系语义核对 · 影响未知',18,fill=AMBER)
    movement=min(1,elapsed/1.6); movement=movement*movement*(3-2*movement)
    split=movement if step==3 else (1 if step==4 else 0)
    trace_progress=max(0,min(1,(elapsed-2)/max(1,duration-3))) if step==3 else (1 if step==4 else 0)
    if step == 3 and trace_fraction is not None:
        trace_progress = trace_fraction
    nodes={n['id']:n for n in plan['nodes']}
    def panel(x,y,w,h,restored=False,alpha=1):
        centers={key:(x+n['x']*w,y+n['y']*h) for key,n in nodes.items()}
        cols=len({n['x'] for n in nodes.values()}); maxrow=max(sum(n['x']==v['x'] for n in nodes.values()) for v in nodes.values())
        nw=min(132+73*max(0,min(1,(w-590)/620)),w*.65/max(1,cols-1)); nh=min(68,h*.6/maxrow)
        def ends(a,b):
            dx,dy=b[0]-a[0],b[1]-a[1]
            t=min((nw/2+5)/max(.01,abs(dx)),(nh/2+5)/max(.01,abs(dy)),.45)
            return (a[0]+dx*t,a[1]+dy*t),(b[0]-dx*t,b[1]-dy*t)
        # Draw source arrows below the stable nodes.
        if step>=1:
            for i,e in enumerate(plan['edges']):
                a,b=centers[e['from']],centers[e['to']]
                f=min(1,max(0,elapsed/.8-i*.5)) if step==1 else 1
                color = BLUE if e['kind'] == 'depends_on' else '#9aa5b8'
                if not dependency and (step==2 or (step>=3 and restored)):
                    color = '#dce2ec'
                arrow(*ends(a,b),color,f)
                label = '依赖' if e['kind']=='depends_on' else e['label']
                text((a[0]+b[0])/2-25,(a[1]+b[1])/2-24,label,16,width=130,lines=1,fill=color)
        visible=set()
        if dependency and restored and step>=3:
            trace=plan['after']['trace']; amount=trace_progress*max(1,len(trace))
            for i,part in enumerate(trace):
                f=min(1,max(0,amount-i))
                if f:
                    a,b=centers[part['from']],centers[part['to']]
                    arrow(*ends((a[0],a[1]+12),(b[0],b[1]+12)),PURPLE,f,5)
                if f>=1:visible.add(part['to'])
        for i,(key,n) in enumerate(nodes.items()):
            cx,cy=centers[key]
            if step==0:
                p=max(0,min(1,(elapsed-i*.25)/1.5));p=p*p*(3-2*p)
                cx=x+w*.5+(cx-x-w*.5)*p;cy=y+h*.85+(cy-y-h*.85)*p
            reached=key in visible
            border=GREEN if reached else BLUE if key==plan['seed'] else '#aebbd0'
            fill='#e8f5ef' if reached else '#fff'
            draw.rounded_rectangle((cx-nw/2,cy-nh/2,cx+nw/2,cy+nh/2),radius=10,fill=fill,outline=border,width=3)
            text(cx-nw/2+10,cy-nh/2+7,n['title'],16,width=nw-20,lines=2)
            if key==plan['seed']:
                text(cx-nw/2,cy+nh/2+4,'起点',16,width=nw,lines=1,fill=BLUE)
        if dependency and step>=2:
            cx,cy=centers[plan['seed']]
            gate=(max(0,min(1,3.4-elapsed)) if restored and step==3 else 0 if restored else min(1,elapsed/1.2) if step==2 else 1)
            if gate>0:
                gx=cx+nw/2+9;draw.line((gx,cy-nh/2,gx,cy-nh/2+nh*gate),fill=AMBER,width=8)
                text(cx-nw/2,cy-nh/2-25,'假设：禁止遍历',16,width=190,lines=1,fill=AMBER)
        if step>=3:
            label = ('恢复遍历' if restored else '假设禁用')+f' · 可达 {len(visible) if restored else len(plan["before"]["reached"])} 项' if dependency else ('依赖证据不足 · 影响未知' if restored else '来源原图 · 保留所有关系')
            text(x+12,y-25,label,20,width=w-24,lines=1,fill=GREEN if restored else AMBER)
    if step<3:
        panel(35,175,1210,340)
    else:
        panel(35,175,1210-620*split,340,False)
        if step==4 or elapsed>1.6:
            background=image.copy()
            panel(655,175,590,340,True)
            alpha=1 if step==4 else min(1,(elapsed-1.6)/.8)
            image=Image.blend(background,image,alpha)
            draw=ImageDraw.Draw(image)
    text(32,550,plan['rule'] if step<3 else plan['limit'],18,width=1210,lines=2,fill='#5c6b82')
    draw.rectangle((0,615,1280,720),fill='#e9eef8')
    text(32,630,caption,24,width=1210,lines=2)
    return image
