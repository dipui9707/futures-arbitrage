import statistics

def percentile(values, current):
    if not values:
        return None
    return 100*(sum(v<current for v in values)+.5*sum(v==current for v in values))/len(values)

def stats(values, current):
    mean=statistics.mean(values)
    std=statistics.pstdev(values)
    change=current-values[0]
    return dict(current=current,change=change,change_pct=100*change/abs(values[0]) if abs(values[0])>1e-12 else None,
                mean=mean,median=statistics.median(values),std=std,zscore=(current-mean)/std if std>1e-12 else None,
                percentile=percentile(values,current),high=max(values),low=min(values),sample_count=len(values))

def histogram(values, bins=24):
    low,high=min(values),max(values)
    width=(high-low)/bins if high>low else 1
    counts=[0]*bins
    for v in values:
        counts[min(bins-1,int((v-low)/width))]+=1
    return [dict(low=low+i*width,high=low+(i+1)*width,count=n) for i,n in enumerate(counts)]

def moving_average(values, window):
    return [statistics.mean(values[i-window+1:i+1]) if i>=window-1 else None for i in range(len(values))]

def state(p):
    return '极端低' if p<=5 else '偏低' if p<25 else '极端高' if p>=95 else '偏高' if p>75 else '中性'
