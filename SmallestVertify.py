"""
注1: 文件路径不能包含中文(但文件包含中文没事)
且 Python 版本大概要低一点, 这里是3.9.25

注2: 需要安装faster-whisper库和opencc库

注3: pip install opencc-python-reimplemented
 ↑ 为避免麻烦, 可以使用基于python的opencc
"""

# =====强调一下能设置的选项~=====
# 这里直接存储处理的文件列表
task_list = []
# 白名单制过滤出能用的格式
valid_exts = ('.mp3','.aac','.wav','.flac','.flv')
# 用于Pushplus推送(注: 若需要分享代码,请记得为token留空)
token = ''
# 开关Pushplus推送(0为关闭, 1为开启)
pushplus_states = 0 
# =============================

# 这里存储程序弹出的小错误, 结尾统一输出
error_list = []

from faster_whisper import WhisperModel
import opencc # 解决whisper模型变繁体幻觉
import threading
import time
import os
import json
import tkinter.messagebox as msgbox

def requests_message():
    msgbox.showwarning("Faster-Whisper",f"输入的文件格式不支持! ")

def exception_message(whathappen):
    msgbox.showwarning("Faster-Whisper",f"在处理时出现了错误!\n{whathappen}")

try:
    import requests
    has_requests = True
except:
    print("检测到未安装 requests库。Skipping~")
    has_requests = False
    pushplus_states = 0 # 强行关闭
    t1 = threading.Thread(target=requests_message)
    t1.start()
    time.sleep(2)

# [PushPlus] 极其安全的独立发送函数
def send_pushplus(title, content):
    # 如果是关闭状态、没token、没装requests, 直接不执行
    if pushplus_states == 0 or not token or not has_requests:
        return -1
    
    try:
        url = "https://www.pushplus.plus/send"
        data = {
            "token": token,
            "title": title,
            "content": content,
            "template": "txt" # 推荐加上这个, 纯文本格式, 手机上看着不乱
        }
        response = requests.post(url, json=data, timeout=5) # 发送 POST 请求
        response.raise_for_status() # 主动捕获状态码是否是200, 不是直接抛HttpError异常
        result_json = response.json()

        # 判断业务状态码
        if result_json.get('code') in (200, "200"):
            print(f"Pushplus 推送成功! \n(消息ID: {result_json.get('data')})")
        else:
            print(f"Pushplus 推送拒绝: {result_json}")
            error_list.append(f"Pushplus 推送拒绝: {result_json}")

    except Exception as e:
        # 就算断网、报错，也只是打印一句话，绝对不影响主程序
        print(f"Pushplus 推送失败: \n{str(e)}")
        error_list.append(f"Pushplus 推送失败: \n{str(e)}")


# 输入部分
input_path = input(r"请你输入文件或文件夹路径: ").strip('"').strip("'")
if not os.path.exists(input_path):
    print("路径不存在,请检查后重试。")
    msgbox.showwarning("Faster-Whisper",f"路径不存在, 请检查后重试。")
    exit()

# 待处理文件部分
if os.path.isfile(input_path):
    if input_path.lower().endswith(valid_exts):
        task_list.append(input_path)
    else:
        print("输入的文件格式不支持! ")
        msgbox.showerror("Faster-Whisper",f"输入的文件格式不支持! ")
        exit()
elif os.path.isdir(input_path):
    for file in os.listdir(input_path):
        if file.lower().endswith(valid_exts):
            task_list.append(os.path.join(input_path, file))
if not task_list:
    print(f"共扫描到 {len(task_list)} 个文件, 程序将自动退出。")
    msgbox.showerror("Faster-Whisper",f"共扫描到 {len(task_list)} 个文件, 程序将自动退出。")
    exit()
print(f"共扫描到 {len(task_list)} 个文件, 准备开始处理。\n")

# 将时间字符串化并且换算
def format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# 加载模型 | 加载转换器
print("正在加载 Whisper 模型, 请稍候...(注: 使用CPU运行)")
model = WhisperModel(r".\faster_whisper_medium", 
    device="cpu", 
    compute_type="int8",
    cpu_threads=2
)
converter = opencc.OpenCC('t2s') # type: ignore (这里vscode会报错,但是实际试了下没事)

# 记录次数
success_count = 0
fail_count = 0

# 主循环部分
for idx, filepath in enumerate(task_list, start=1):
    print(f"\n[{idx} / {len(task_list)}] 开始处理: {filepath}\n\n\n")
    try:
        file_dir = os.path.dirname(filepath) # 去文件名得其[上级目录]
        base_filename = os.path.basename(filepath) # 获取[文件名]
        source_name, _ = os.path.splitext(base_filename) # 分离文件名的[名字]与[后缀]
        formatted_time = time.strftime("%Y%m%d%H%M%S", time.localtime())

        # 这里使用os.path.join,兼顾跨平台特性(DeepSeek可以啊)
        srt_path = os.path.join(file_dir, f"{source_name}_{formatted_time}.srt")
        txt_path = os.path.join(file_dir, f"{source_name}_{formatted_time}.txt")

        # 核心部分: 丢给 AI 识别
        segments, info = model.transcribe(filepath, language="zh", vad_filter=True)
        print(f"识别语言: {info.language}\n 概率: {info.language_probability}")

        # 哈哈哈, 同步写入 str 和 txt 
        # 这么写, 一个是为了方便丢给AI总结, 另一个是为了剪辑软件方便
        with open(srt_path, "w", encoding="utf-8") as f_srt, \
            open(txt_path, "w", encoding="utf-8") as f_txt:

                for seg_idx, seg in enumerate(segments, start=1):
                    sc_text = converter.convert(seg.text.strip())

                    f_srt.write(f"{seg_idx}\n")  # 第几句话
                    f_srt.write(f"{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n") # 时间轴
                    f_srt.write(sc_text + "\n\n") # 文字内容(strip()是去掉两边的空格和换行)

                    # txt 只输出纯文本,不包含时间轴
                    f_txt.write(sc_text + "\n")

                    # 打印进度
                    print(f"·当前队列: {idx} / {len(task_list)}")
                    print(f"-->[{seg.start:.2f}-{seg.end:.2f}] {sc_text}")

        success_count += 1
        print(f"处理完成: {source_name} , 还剩下: {len(task_list) - idx}")

    except Exception as e:
        fail_count += 1
        print(f"处理失败: {filepath}\n错误信息: {e}")
        error_list.append(f"处理失败: {filepath}\n错误信息: {e}")
        t2 = threading.Thread(target=exception_message,args=(str(e),))
        t2.start()
        time.sleep(1)
        continue

# 跑完了, 进行总结输出
summary_msg = f"共处理{len(task_list)}个文件\n成功: {success_count}\n失败: {fail_count}"
print(f"\n{summary_msg}")
# 如果有错误，统一打印出来！
if error_list:
    print("\n========= 错误日志 =========")
    for err in error_list:
        print(err)
    print("============================")
    all_errors = "\n".join(error_list)
    send_pushplus("Faster-Whisper", f"错误汇总如下:\n" + \
                f"有 {len(error_list)} 个报错: \n{all_errors}" + \
                f"\n\n{summary_msg}")
else:
    send_pushplus("Faster-Whisper", f"{summary_msg}")
msgbox.showinfo("Faster-Whisper",summary_msg)