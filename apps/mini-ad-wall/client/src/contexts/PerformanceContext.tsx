import { createContext, useContext, useState, type ReactNode } from 'react';
import { performanceForAd, type PerformanceRange } from '../utils/performance';
import type { Ad } from '../types';
const Context = createContext<{range:PerformanceRange;setRange:(range:PerformanceRange)=>void}>({range:'7',setRange:()=>{}});
export function PerformanceProvider({children}:{children:ReactNode}) {
    const [range,setRange]=useState<PerformanceRange>('7');
    return <Context.Provider value={{range,setRange}}>{children}</Context.Provider>;
}
export function usePerformance() {
    const context=useContext(Context);
    return {...context, measures:(ad:Ad)=>performanceForAd(ad,context.range),period:context.range==='7'?'10 月 1–7 日':'10 月 5–7 日'};
}
