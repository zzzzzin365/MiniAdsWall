import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { withAdsConnection } from './ads.database';
import { AdInput } from '../types';
import { currentAdsConnection, recordOperationChange } from '../models/ads.model';
const decode = (v: any) => typeof v === 'string' ? JSON.parse(v) : v;
// Material snapshots are immutable. Editing content produces another snapshot;
// other ads using the original material keep their approved content.
export async function resolveMaterial(c: PoolConnection, input: AdInput, images?: string[]) {
    if (input.material_id) {
        const [rows] = await c.execute<RowDataPacket[]>('SELECT * FROM ads_business_materials WHERE id=?', [input.material_id]);
        if (!rows.length) throw Object.assign(new Error('素材不存在，请刷新'), {status: 404});
        const row = rows[0];
        if (row.content !== input.content || JSON.stringify(decode(row.videos)) !== JSON.stringify(input.videos || []))
            throw Object.assign(new Error('素材内容已修改，请取消素材选择后保存为新素材'), {status: 409});
        return row.id as string;
    }
    const videos = JSON.stringify(input.videos || []);
    const [hash] = await c.execute<RowDataPacket[]>('SELECT SHA2(CONCAT(?,CHAR(0),CAST(CAST(? AS JSON) AS CHAR),?),256) AS id', [input.content, videos, images?.length ? '\0images:'+JSON.stringify(images) : '']);
    const id = hash[0].id;
    await c.execute('INSERT INTO ads_business_materials(id,name,content,videos) VALUES (?,?,?,?) ON DUPLICATE KEY UPDATE id=id', [id,input.title,input.content,videos]);
    if (images) await c.execute('INSERT INTO ads_business_material_images(material_id,images) VALUES (?,?) ON DUPLICATE KEY UPDATE images=VALUES(images)',[id,JSON.stringify(images)]);
    return id as string;
}
export async function bindMaterial(c: PoolConnection, adId: string, id: string) {
    await c.execute('INSERT INTO ads_business_material_links(ad_id,material_id) VALUES (?,?) ON DUPLICATE KEY UPDATE material_id=VALUES(material_id)', [adId,id]);
}
export async function backfillMaterials(c: PoolConnection) {
    const [rows] = await c.query<RowDataPacket[]>('SELECT a.* FROM ads_business_ads a LEFT JOIN ads_business_material_links l ON l.ad_id=a.id WHERE l.ad_id IS NULL');
    for (const row of rows) await bindMaterial(c,row.id,await resolveMaterial(c,{...row,videos:decode(row.videos)} as AdInput));
}
export async function listMaterials(cursor = '') {
    if (cursor && !/^[a-f0-9]{64}$/.test(cursor)) throw Object.assign(new Error('素材分页无效'), {status:400});
    return withAdsConnection(async c => {
        const [rows] = await c.query<RowDataPacket[]>(`SELECT m.*, (SELECT i.images FROM ads_business_material_images i WHERE i.material_id=m.id) AS images, (SELECT COUNT(*) FROM ads_business_material_links l WHERE l.material_id=m.id) AS usage_count FROM ads_business_materials m WHERE m.id>? AND NOT EXISTS (SELECT 1 FROM ads_business_material_archive d WHERE d.material_id=m.id) ORDER BY m.id LIMIT 51`, [cursor]);
        const items: any[] = rows.slice(0,50).map(row=>({...row,videos:decode(row.videos),images:decode(row.images)||[],usage_count:Number(row.usage_count)}));
        if (items.length) {
            const [usage] = await c.query<RowDataPacket[]>(`SELECT l.material_id,a.id,JSON_LENGTH(a.videos) AS video_count FROM ads_business_material_links l JOIN ads_business_ads a ON a.id=l.ad_id WHERE l.material_id IN (${items.map(()=>'?').join(',')}) ORDER BY a.id`,items.map(item=>item.id));
            for (const item of items) item.usage_ads = usage.filter(ad=>ad.material_id===item.id).map(ad=>({id:ad.id,video_count:Number(ad.video_count)}));
        }
        return {items,next_cursor:rows.length>50?items[items.length-1].id:null};
    });
}

function validId(id: string) { if (!/^[a-f0-9]{64}$/.test(id)) throw Object.assign(new Error('素材 ID 无效'), {status:400}); }
export async function getMaterial(id: string) {
    validId(id);
    return withAdsConnection(async c => {
        const [rows] = await c.execute<RowDataPacket[]>('SELECT m.*, (SELECT i.images FROM ads_business_material_images i WHERE i.material_id=m.id) AS images FROM ads_business_materials m WHERE m.id=? AND NOT EXISTS (SELECT 1 FROM ads_business_material_archive d WHERE d.material_id=m.id)',[id]);
        if (!rows.length) throw Object.assign(new Error('素材不存在或已删除'),{status:404});
        return {...rows[0],videos:decode(rows[0].videos),images:decode(rows[0].images)||[]};
    });
}
export async function changeMaterial(id: string, input?: {name: string; content: string; videos: string[]; images?: string[]}) {
    validId(id);
    if(input) validateMaterialInput(input);
    const c=currentAdsConnection();
    const [rows]=await c.execute<RowDataPacket[]>('SELECT * FROM ads_business_materials WHERE id=? FOR UPDATE',[id]);
    const [archived]=await c.execute<RowDataPacket[]>('SELECT material_id FROM ads_business_material_archive WHERE material_id=?',[id]);
    if (!rows.length || archived.length) throw Object.assign(new Error('素材不存在或已删除，请刷新'),{status:404});
    const [imageRows]=await c.execute<RowDataPacket[]>('SELECT images FROM ads_business_material_images WHERE material_id=?',[id]);
    const before={...rows[0],videos:decode(rows[0].videos),images:imageRows.length?decode(imageRows[0].images):[]};
    if (!input) {
        await c.execute('INSERT INTO ads_business_material_archive(material_id) VALUES (?)',[id]);
        recordOperationChange(before,{id,archived:true});return {id,archived:true};
    }
    const images=input.images??(imageRows.length?decode(imageRows[0].images):[]);
    const nextId=await resolveMaterial(c,{title:input.name.trim(),content:input.content,videos:input.videos} as AdInput,images);
    await c.execute('UPDATE ads_business_materials SET name=? WHERE id=?',[input.name.trim(),nextId]);
    await c.execute('DELETE FROM ads_business_material_archive WHERE material_id=?',[nextId]);
    if (nextId!==id) await c.execute('INSERT INTO ads_business_material_archive(material_id) VALUES (?)',[id]);
    const result={id:nextId,name:input.name.trim(),content:input.content,videos:input.videos,images};
    recordOperationChange(before,result);return result;
}

function validateMaterialInput(input: {name:string;content:string;videos:string[];images?:string[]}) {
    if ((typeof input.name !== 'string' || !input.name.trim() || input.name.length>500 || typeof input.content !== 'string' || Buffer.byteLength(input.content)>1024*1024 || !Array.isArray(input.videos) || input.videos.length>20 || input.videos.some(v=>typeof v!=='string'||v.length>4096||!(/^(https?:\/\/|\/)/.test(v))))) throw Object.assign(new Error('素材名称、正文或视频地址无效'),{status:400});
    if (input?.images!==undefined && (!Array.isArray(input.images)||input.images.length+input.videos.length>20||input.images.some(v=>typeof v!=='string'||v.length>4096||!(/^(https?:\/\/|\/)/.test(v))))) throw Object.assign(new Error('图片地址或素材数量无效'),{status:400});
    if (!input.content.trim() && !input.videos.length && !input.images?.length) throw Object.assign(new Error('请填写文案或上传素材'),{status:400});
}
export async function createMaterial(input: {name:string;content:string;videos:string[];images?:string[]}) {
    validateMaterialInput(input);
    const c=currentAdsConnection(),images=input.images||[];
    const id=await resolveMaterial(c,{title:input.name.trim(),content:input.content,videos:input.videos} as AdInput,images);
    await c.execute('UPDATE ads_business_materials SET name=? WHERE id=?',[input.name.trim(),id]);
    await c.execute('DELETE FROM ads_business_material_archive WHERE material_id=?',[id]);
    const result={id,name:input.name.trim(),content:input.content,videos:input.videos,images};recordOperationChange(null,result);return result;
}
