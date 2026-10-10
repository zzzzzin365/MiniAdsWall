// Persistent AVL: readers keep an immutable root while writes replace O(log N) nodes.
export class Node<T> {
 readonly height: number;
 constructor(readonly value:T,readonly left:Node<T>|null=null,readonly right:Node<T>|null=null) { this.height=1+Math.max(left?.height||0,right?.height||0); }
}
const height=<T>(n:Node<T>|null)=>n?.height||0;
function right<T>(n:Node<T>):Node<T> { const l=n.left!;return new Node(l.value,l.left,new Node(n.value,l.right,n.right)); }
function left<T>(n:Node<T>):Node<T> { const r=n.right!;return new Node(r.value,new Node(n.value,n.left,r.left),r.right); }
function balance<T>(n:Node<T>):Node<T> {
 const b=height(n.left)-height(n.right);
 if(b>1) { if(height(n.left!.left)<height(n.left!.right)) n=new Node(n.value,left(n.left!),n.right);return right(n); }
 if(b< -1) { if(height(n.right!.right)<height(n.right!.left)) n=new Node(n.value,n.left,right(n.right!));return left(n); }
 return n;
}
export function put<T>(n:Node<T>|null,v:T,cmp:(a:T,b:T)=>number):Node<T> {
 if(!n)return new Node(v);const c=cmp(v,n.value);
 return balance(c===0?new Node(v,n.left,n.right):c<0?new Node(n.value,put(n.left,v,cmp),n.right):new Node(n.value,n.left,put(n.right,v,cmp)));
}
export function remove<T>(n:Node<T>|null,v:T,cmp:(a:T,b:T)=>number):Node<T>|null {
 if(!n)return null;const c=cmp(v,n.value);
 if(c<0)return balance(new Node(n.value,remove(n.left,v,cmp),n.right));
 if(c>0)return balance(new Node(n.value,n.left,remove(n.right,v,cmp)));
 if(!n.left)return n.right;if(!n.right)return n.left;
 let successor=n.right;while(successor.left)successor=successor.left;
 return balance(new Node(successor.value,n.left,remove(n.right,successor.value,cmp)));
}
export function find<T>(n:Node<T>|null,v:T,cmp:(a:T,b:T)=>number):T|undefined { while(n) {const c=cmp(v,n.value);if(!c)return n.value;n=c<0?n.left:n.right;} }
export function* iterate<T>(root:Node<T>|null):Generator<T> { const stack:Node<T>[]=[];let n=root;while(n||stack.length) {while(n){stack.push(n);n=n.left;}n=stack.pop()!;yield n.value;n=n.right;} }
export function top<T>(items:Iterable<T>,k:number,cmp:(a:T,b:T)=>number):T[] {
 const heap:T[]=[];
 const down=()=>{let i=0;while(true){let j=i*2+1;if(j>=heap.length)break;if(j+1<heap.length&&cmp(heap[j+1],heap[j])>0)j++;if(cmp(heap[i],heap[j])>=0)break;[heap[i],heap[j]]=[heap[j],heap[i]];i=j;}};
 for(const v of items) {
  if(heap.length<k){heap.push(v);let i=heap.length-1;while(i){const p=(i-1)>>1;if(cmp(heap[p],heap[i])>=0)break;[heap[p],heap[i]]=[heap[i],heap[p]];i=p;}}
  else if(cmp(v,heap[0])<0){heap[0]=v;down();}
 }
 return heap.sort(cmp);
}
