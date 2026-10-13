import type { ReactNode } from 'react';
import type { Ad } from '../types';
import MarketingDemo from './MarketingDemo';
import './MarketingDashboard.css';

export default function MarketingDashboard({ ads, loadMore, adStatus }: { ads: Ad[]; loadMore?: ReactNode; adStatus?: ReactNode }) {
    return <section className="marketing-dashboard marketing-dashboard-demo" aria-label="投放效果指标">
        <MarketingDemo ads={ads} loadMore={loadMore} adStatus={adStatus} />
    </section>;
}
