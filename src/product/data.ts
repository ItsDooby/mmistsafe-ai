import {useEffect,useState} from 'react';
export type Sample={id:string;category:string;temperature:number;ph:number;ec:number;turbidity:number;flow:number;stagnation:number;chlorine:number;uv:boolean;sensorOk:boolean;stop:boolean;referencePositive:boolean;method:string;sensorScore:number;contributions:number[];imageScore:number|null;imageUrl:string|null;imageQuality:string};
export type Settings={moderate:number;high:number;riskAlerts:boolean;riskSeverity:'warning'|'critical';sensorWeight:number;unit:'C'|'F';interval:number;playing:boolean};
export const defaults:Settings={moderate:30,high:60,riskAlerts:true,riskSeverity:'warning',sensorWeight:70,unit:'C',interval:15,playing:true};
export function stored<T>(key:string,fallback:T):T{try{const v=JSON.parse(localStorage.getItem(key)||'null');return v??fallback}catch{return fallback}}
export function persist(key:string,value:unknown){try{localStorage.setItem(key,JSON.stringify(value));return true}catch{return false}}
const cache=new Map<string,Promise<any>>();
export function loadData<T>(name:string):Promise<T>{if(!cache.has(name))cache.set(name,fetch(`${import.meta.env.BASE_URL}analysis/${name}.json`).then(r=>{if(!r.ok)throw Error('Analysis could not be loaded');return r.json()}).catch(e=>{cache.delete(name);throw e}));return cache.get(name)!}
export function band(score:number,s:Settings){return score>=s.high?'high':score>=s.moderate?'moderate':'low'}
export function useAnalysis(){
 const [samples,setSamples]=useState<Sample[]>([]),[error,setError]=useState(false),[revision,retry]=useState(0);
 const [settings,setSettings]=useState<Settings>(()=>{const v=stored<Partial<Settings>>('ms-settings-v3',{});const m=Number(v.moderate),h=Number(v.high);return {...defaults,...v,moderate:m>=1&&m<h?m:30,high:h>m&&h<=100?h:60,interval:[5,15,30,60].includes(Number(v.interval))?Number(v.interval):15}});
 const [index,setIndex]=useState(0),[updated,setUpdated]=useState(Date.now()),[now,setNow]=useState(Date.now());
 const [storageOk,setStorageOk]=useState(true);
 useEffect(()=>{let active=true;setError(false);loadData<Sample[]>('samples').then(v=>{if(active){setSamples(v);setUpdated(Date.now())}}).catch(()=>active&&setError(true));return()=>{active=false}},[revision]);
 useEffect(()=>{setStorageOk(persist('ms-settings-v3',settings))},[settings]);
 useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id)},[]);
 useEffect(()=>{if(!samples.length||!settings.playing)return;const id=setInterval(()=>{setIndex(i=>(i+1)%samples.length);setUpdated(Date.now())},settings.interval*1000);return()=>clearInterval(id)},[samples.length,settings.interval,settings.playing]);
 function select(id:string){const i=samples.findIndex(s=>s.id===id);if(i>=0){setIndex(i);setUpdated(Date.now())}}
 return {samples,sample:samples[index],settings,setSettings,seconds:Math.max(0,Math.floor((now-updated)/1000)),error,retry:()=>retry(n=>n+1),select,storageOk};
}
