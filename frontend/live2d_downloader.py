import io
import os
import shutil
import sys
import tempfile
import zipfile

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

os.chdir(os.path.dirname(os.path.abspath(__file__)))
def download_and_extract_live2d_model(url, frontend_2d_path):

    """下载并解压Live2D模型

    Args:
        url (str): Live2D模型的下载链接
        frontend_2d_path (str): 前端2D模型的路径
    """
    # 下载文件
    print(f"[Live2dDownloader]正在下载Live2D模型: {url},这可能需要一些时间，请耐心等待...")
    response = requests.get(url)
    extract_to = tempfile.mkdtemp()
    print(f"[Live2dDownloader]下载完成，正在解压到临时目录: {extract_to}")
    if response.status_code == 200:
        os.makedirs(extract_to, exist_ok=True)
        os.makedirs(frontend_2d_path, exist_ok=True)
        zip_path = os.path.join(extract_to, "live2d_model.zip")
        with open(zip_path, "wb") as f:
            f.write(response.content)
        # 解压文件
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
            dirname=zip_ref.namelist()[0].split("/")[0]  # 获取解压后的文件夹名称
        # 将解压后的模型文件复制到前端2D模型路径
            print(dirname)
            shutil.copytree(os.path.join(os.path.join(extract_to,dirname), "runtime"), os.path.join(frontend_2d_path, dirname), dirs_exist_ok=True)
        os.remove(zip_path)
        shutil.rmtree(extract_to)
        print(f"[Live2dDownloader]模型下载并解压完成，已复制到前端路径: {frontend_2d_path}")
    else:
        print(f"[Live2dDownloader]下载失败，状态码: {response.status_code}")
if __name__ == "__main__":
    print("[Live2dDownloader]开始下载和解压Live2D模型...")
    print("警告:在运行这个程序时，你被视为同意Live2D Cubsim 示例数据的使用条款，参见https://cubism.live2d.com，如果不同意，请立刻关闭程序并删除下载的模型文件。")
    #download_and_extract_live2d_model("https://cubism.live2d.com/sample-data/bin/tororohijiki/tororo_hijiki.zip", "./2D")
    #download_and_extract_live2d_model(
    #    url="https://cubism.live2d.com/sample-data/bin/ren_pro/ren_pro_zh.zip",
    #    frontend_2d_path="./2D")
    download_and_extract_live2d_model(
       url="https://cubism.live2d.com/sample-data/bin/hiyori_pro/hiyori_pro_zh.zip",
       frontend_2d_path="./models/2D")
    print("[Live2dDownloader]所有模型下载和解压完成。")