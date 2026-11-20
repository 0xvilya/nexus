export interface Artifact { id: string; name: string; path: string; sha256: string; bytes: number; verified_at: string }
export interface Task { id: string; description: string; status: string; agent: string; dependencies: string[] }
export interface Workflow { id: string; goal: string; status: string; created_at: string; graph: Task[]; error: string | null; artifacts?: Artifact[] }
export interface Model { category?: string; compatibility?: string; projector?: {filename:string}; id: string; name: string; filename: string; parameters: string; quantization: string; bytes: number; license: string; license_url: string; downloaded: boolean; ram_estimate_bytes: number; source: string }
export interface Snapshot {
  conversations?:{items:{id:string;title:string;messages:number}[];has_more:boolean};
  action_tasks?:{id:string;goal:string;state:string;route?:'chat'|'task'|'agent';turn_seq?:number;conversation_id?:string|null}[];
  workflows: Workflow[]; artifacts: Artifact[]; workspace: string; version: string;
  hardware: { os: string; logical_cpus: number; ram_bytes: number | null; free_disk_bytes: number; gpu: string; backends: string[] };
  runtime: { state: string; model: string | null; backend: string; context?: number | null; runtime_installed: boolean };
  models: Model[];
  installation: { state: string; model_id?: string; downloaded: number; total: number; error: string | null };
}
export interface ChatResult { vision_description?: string; text: string; state: string; error: string | null }
