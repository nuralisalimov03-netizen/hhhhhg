/* Bounded parser + exact rationals/polynomials. CAS is never a JS evaluator. */
'use strict';
const MAX_NODES=192, MAX_DEPTH=24, MAX_BITS=8192, MAX_POWER=64;
const FUNCTIONS=new Set(['sin','cos','tan','asin','acos','atan','sqrt','abs','log','exp']);
const ASSET_BASE=self.WEQROM_MATH_ASSET_BASE || new URL('.',self.location.href).href;
const fail=message=>{throw new Error(message);};
const abs=n=>n<0n?-n:n;
function gcd(a,b){a=abs(a);b=abs(b);while(b){const r=a%b;a=b;b=r;}return a;}
function rational(n,d=1n){
  if(d===0n)fail('Деление на ноль недопустимо.');
  if(d<0n){n=-n;d=-d;}
  if(abs(n).toString(2).length>MAX_BITS || d.toString(2).length>MAX_BITS)fail('Числа слишком велики. Упростите выражение.');
  const g=gcd(n,d);return {n:n/g,d:d/g};
}
const ZERO=rational(0n),ONE=rational(1n);
const plus=(a,b)=>rational(a.n*b.d+b.n*a.d,a.d*b.d);
const minus=(a,b)=>rational(a.n*b.d-b.n*a.d,a.d*b.d);
const times=(a,b)=>rational(a.n*b.n,a.d*b.d);
const divide=(a,b)=>rational(a.n*b.d,a.d*b.n);
const negative=a=>rational(-a.n,a.d);
const text=a=>a.d===1n?String(a.n):String(a.n)+'/'+String(a.d);
function power(a,b){
  if(b.d!==1n || abs(b.n)>BigInt(MAX_POWER))fail('Степень должна быть целой от −64 до 64.');
  if(a.n===0n && b.n<=0n)fail('Ноль в нулевой или отрицательной степени не определён.');
  const amount=abs(b.n);
  if((abs(a.n).toString(2).length+ a.d.toString(2).length)*Number(amount)>MAX_BITS*2)fail('Результат степени слишком велик.');
  return b.n<0n?rational(a.d**amount,a.n**amount):rational(a.n**amount,a.d**amount);
}
function number(source){
  const parts=source.toLowerCase().split('e');
  const mantissa=parts[0], exponent=parts.length===2?Number(parts[1]):0;
  if(!Number.isInteger(exponent)||Math.abs(exponent)>64)fail('Порядок числа слишком велик: максимум 64.');
  const digits=mantissa.replace('.','');
  if(digits.length>60)fail('В числе разрешено не больше 60 цифр.');
  const decimals=mantissa.includes('.')?mantissa.length-mantissa.indexOf('.')-1:0;
  const shift=exponent-decimals;
  return shift>=0?rational(BigInt(digits)*10n**BigInt(shift)):rational(BigInt(digits),10n**BigInt(-shift));
}
function parse(expression,mode,variable){
  if(/[²³](?=\s*(?:[0-9²³]|\.\d))/.test(expression))fail('После ² или ³ нужен знак операции. Многозначные степени вводите через ^.');
  const source=expression.replace(/[×·]/g,'*').replace(/÷/g,'/').replace(/[−–]/g,'-').replace(/π/g,'pi').replace(/²/g,'^2').replace(/³/g,'^3').replace(/(\d),(?=\d)/g,'$1.');
  const tokens=[];let cursor=0;
  while(cursor<source.length){
    const rest=source.slice(cursor),space=/^\s+/.exec(rest);
    if(space){cursor+=space[0].length;continue;}
    const numeric=/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/.exec(rest);
    const name=/^[A-Za-z_][A-Za-z_0-9]*/.exec(rest);
    if(numeric){
      if(tokens.length&&tokens[tokens.length-1].type==='number')fail('Между числами нужен знак операции. Не разделяйте разряды пробелами.');
      tokens.push({type:'number',value:numeric[0]});cursor+=numeric[0].length;
    }
    else if(name){tokens.push({type:'name',value:name[0]});cursor+=name[0].length;}
    else if('+-*/^()='.includes(rest[0])){tokens.push({type:rest[0],value:rest[0]});cursor++;}
    else fail('Недопустимый символ в математическом выражении.');
    if(tokens.length>MAX_NODES)fail('В выражении слишком много операций.');
  }
  tokens.push({type:'end'});let index=0,nodes=0;
  const peek=()=>tokens[index];
  function node(value){if(++nodes>MAX_NODES)fail('В выражении слишком много операций.');return value;}
  function expressionNode(minimum=0,depth=0){
    if(depth>MAX_DEPTH)fail('Слишком много вложенных скобок или степеней.');
    const token=tokens[index++];let left;
    if(token.type==='number')left=node({type:'number',value:number(token.value)});
    else if(token.type==='+'||token.type==='-')left=node({type:'unary',op:token.type,arg:expressionNode(25,depth+1)});
    else if(token.type==='('){left=expressionNode(0,depth+1);if(peek().type!==')')fail('Не хватает закрывающей скобки.');index++;}
    else if(token.type==='name'){
      if(FUNCTIONS.has(token.value)){
        if(peek().type!=='(')fail('Аргумент функции нужно заключить в скобки.');index++;
        const arg=expressionNode(0,depth+1);if(peek().type!==')')fail('Функция принимает один аргумент в скобках.');index++;
        left=node({type:'call',name:token.value,arg});
      }else if(token.value===variable && mode!=='calculate')left=node({type:'variable',name:variable});
      else if(token.value==='pi'||token.value==='e')left=node({type:'constant',name:token.value});
      else fail('Неизвестное имя: '+token.value.slice(0,20)+'.');
    }else fail('Ожидается число, переменная или открывающая скобка.');
    while(true){
      const next=peek();let op=next.type,precedence=op==='+'||op==='-'?10:op==='*'||op==='/'?20:op==='^'?30:-1;
      const implicit=next.type==='number'||next.type==='name'||next.type==='(';
      if(implicit){op='*';precedence=20;}
      if(precedence<minimum)break;
      if(!implicit)index++;
      const right=expressionNode(op==='^'?precedence:precedence+1,depth+1);
      left=node({type:'binary',op,left,right});
    }
    return left;
  }
  const left=expressionNode();
  if(mode==='equation'){
    if(peek().type!=='=')fail('Введите уравнение с одним знаком =.');index++;
    const right=expressionNode();if(peek().type!=='end')fail('В уравнении должен быть ровно один знак =.');
    return {left,right};
  }
  if(peek().type!=='end')fail('Проверьте скобки и знаки операций.');
  return left;
}
function hasVariable(ast){return ast.type==='variable'||(ast.arg&&hasVariable(ast.arg))||(ast.left&&(hasVariable(ast.left)||hasVariable(ast.right)))||false;}
function exact(ast){
  if(ast.type==='number')return ast.value;
  if(ast.type==='unary'){const a=exact(ast.arg);return ast.op==='-'?negative(a):a;}
  if(ast.type==='binary'){
    const a=exact(ast.left),b=exact(ast.right);
    return ast.op==='+'?plus(a,b):ast.op==='-'?minus(a,b):ast.op==='*'?times(a,b):ast.op==='/'?divide(a,b):power(a,b);
  }
  fail('Для этой операции требуется обычное число.');
}
function numericOnly(ast){return ast.type==='number'||(ast.type==='unary'&&numericOnly(ast.arg))||(ast.type==='binary'&&numericOnly(ast.left)&&numericOnly(ast.right));}
function validate(ast){
  if(ast.type==='number'||ast.type==='variable'||ast.type==='constant')return;
  if(ast.arg){validate(ast.arg);return;}
  validate(ast.left);validate(ast.right);
  if(ast.op==='^'){
    const exponent=exact(ast.right);
    if(exponent.d!==1n || abs(exponent.n)>BigInt(MAX_POWER))fail('Степень должна быть целой от −64 до 64.');
    if(exponent.n===0n&&hasVariable(ast.left)){
      let nonzeroConstant=false;
      try{const base=polynomial(ast.left);nonzeroConstant=base.c.length===1&&base.c[0].n!==0n;}catch(_){ }
      if(!nonzeroConstant)fail('Нулевая степень выражения с переменной может дать 0^0. Уточните область определения.');
    }
  }
  if(numericOnly(ast))exact(ast); // Bound arithmetic and reject zero divisors before CAS.
}
function serialize(ast){
  if(ast.type==='number')return '('+text(ast.value)+')';
  if(ast.type==='variable'||ast.type==='constant')return ast.name;
  if(ast.type==='unary')return '('+ast.op+serialize(ast.arg)+')';
  if(ast.type==='call')return ast.name+'('+serialize(ast.arg)+')';
  return '('+serialize(ast.left)+ast.op+serialize(ast.right)+')';
}
function cas(expression){
  if(!self.nerdamer)importScripts(new URL('vendor/nerdamer.min.js',ASSET_BASE).href);
  return self.nerdamer(expression);
}
function polynomial(ast){
  if(ast.type==='number')return {c:[ast.value],variable:false};
  if(ast.type==='variable')return {c:[ZERO,ONE],variable:true};
  if(ast.type==='constant'||ast.type==='call')fail('В уравнениях поддерживаются только полиномы с числовыми коэффициентами.');
  if(ast.type==='unary'){const value=polynomial(ast.arg);return {c:ast.op==='-'?value.c.map(negative):value.c,variable:value.variable};}
  const a=polynomial(ast.left),b=polynomial(ast.right),variable=a.variable||b.variable;
  function trim(c){while(c.length>1&&c[c.length-1].n===0n)c.pop();return c;}
  function multiply(c,d){
    if(c.length+d.length-2>2)fail('Поддерживаются уравнения степени не выше 2.');
    const result=Array.from({length:c.length+d.length-1},()=>ZERO);
    for(let i=0;i<c.length;i++)for(let j=0;j<d.length;j++)result[i+j]=plus(result[i+j],times(c[i],d[j]));
    return trim(result);
  }
  if(ast.op==='+'||ast.op==='-'){
    const c=[];for(let i=0;i<Math.max(a.c.length,b.c.length);i++)c.push(ast.op==='+'?plus(a.c[i]||ZERO,b.c[i]||ZERO):minus(a.c[i]||ZERO,b.c[i]||ZERO));
    return {c:trim(c),variable};
  }
  if(ast.op==='*')return {c:multiply(a.c,b.c),variable};
  if(ast.op==='/'){
    if(b.variable)fail('Переменная в знаменателе не поддерживается, даже если её можно сократить.');
    return {c:a.c.map(value=>divide(value,b.c[0])),variable};
  }
  if(b.variable)fail('Показатель степени в уравнении должен быть постоянным числом.');
  const exponent=b.c[0];
  if(exponent.d!==1n||abs(exponent.n)>BigInt(MAX_POWER))fail('Степень должна быть целой от −64 до 64.');
  if(exponent.n===0n&&a.c.length!==1)fail('Нулевая степень выражения с переменной может дать 0^0. Уточните область определения.');
  if(exponent.n<0n){
    if(a.variable)fail('Переменная в знаменателе или отрицательной степени не поддерживается.');
    return {c:[power(a.c[0],exponent)],variable};
  }
  if(a.c.length===1)return {c:[power(a.c[0],exponent)],variable};
  if(BigInt(a.c.length-1)*exponent.n>2n)fail('Поддерживаются уравнения степени не выше 2.');
  let c=[ONE];for(let i=0;i<Number(exponent.n);i++)c=multiply(c,a.c);
  return {c,variable};
}
function solveEquation(ast,variable){
  const left=polynomial(ast.left),right=polynomial(ast.right),c=[];
  for(let i=0;i<Math.max(left.c.length,right.c.length);i++)c.push(minus(left.c[i]||ZERO,right.c[i]||ZERO));
  while(c.length<3)c.push(ZERO);
  const constant=c[0],b=c[1],a=c[2];
  const steps=['Переносим все члены в левую часть и приводим подобные.', 'a = '+text(a)+', b = '+text(b)+', c = '+text(constant)+'.'];
  if(a.n===0n){
    if(b.n===0n)return {text:constant.n===0n?'Тождество: любое значение '+variable+'.':'Решений нет.',steps,exact:true};
    const solution=divide(negative(constant),b);steps.push(variable+' = −c / b.');
    return {text:variable+' = '+text(solution),steps,exact:true};
  }
  const d=minus(times(b,b),times(rational(4n),times(a,constant)));
  steps.push('D = b² − 4ac = '+text(d)+'.');
  steps.push(variable+' = (−b ± √D) / (2a).');
  const center=negative(b),denominator=times(rational(2n),a);
  const root=sign=>cas('(('+text(center)+')'+sign+'sqrt(('+text(d)+')))/('+text(denominator)+')').toString();
  const first=root('+'),second=root('-');
  const roots=first===second?[first]:[first,second];
  let result=roots.map(value=>variable+' = '+value).join('; ');
  if(d.n<0n)result='Действительных корней нет. Комплексные корни: '+result;
  return {text:result,steps,exact:true};
}
function calculate(request){
  if(!request||typeof request.expression!=='string'||request.expression.length>512)fail('Выражение слишком длинное или отсутствует.');
  const mode=request.mode||'calculate',variable=request.variable||'x';
  if(!['calculate','equation','derivative'].includes(mode)||!/^[a-df-hj-z]$/.test(variable))fail('Неизвестный режим или переменная.');
  const ast=parse(request.expression,mode,variable);
  if(mode==='equation')return solveEquation(ast,variable);
  validate(ast);
  if(mode==='calculate'&&numericOnly(ast))return {text:text(exact(ast)),steps:['Точный расчёт с рациональными числами без округления.'],exact:true};
  const expression=serialize(ast);
  const value=mode==='derivative'?cas('diff('+expression+','+variable+')'):cas(expression);
  let result=value.toString();
  if(result.length>4096)fail('Результат слишком длинный. Упростите выражение.');
  if(mode==='calculate'){
    try{const approximate=value.evaluate().text('decimals',16);if(approximate!==result&&approximate.length<200&&/^[0-9eE+\-.]+$/.test(approximate))result+='\n≈ '+approximate;}catch(_){ }
  }
  return {text:result,steps:[mode==='derivative'?'Символьная производная по '+variable+'.':'Автономный математический движок; точная форма сохранена.'],exact:true};
}
self.onmessage=event=>{
  const data=event.data||{};let result;
  try{result=calculate(data.request);}catch(error){result={text:'',error:error&&error.message?String(error.message).slice(0,240):'Не удалось выполнить расчёт.'};}
  self.postMessage({id:data.id,result});
};
