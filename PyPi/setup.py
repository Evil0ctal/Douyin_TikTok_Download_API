#! /usr/bin/env python
# -*- coding: utf-8 -*-
# RUN Command Line:
# python3 setup.py sdist (Build-check dist folder)
# python3 -m twine upload --repository pypi dist/*   (Upload to PyPi)

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup
import setuptools

setup(
    name='DT_Scraper',  # 包的名字
    author='Evil0ctal',  # 作者
    version='1.0.0',  # 版本号
    license='MIT',

    description='Douyin/TikTok crawler and no watermark video download.',  # 描述
    long_description='''Douyin/TikTok crawler and no watermark video download.''',
    author_email='evil0ctal1985@gmail.com',  # 你的邮箱**
    url='https://github.com/Evil0ctal/Douyin_TikTok_Download_API',  # 可以写github上的地址，或者其他地址
    # 包内需要引用的文件夹
    # packages=setuptools.find_packages(exclude=['url2io',]),
    packages=["DT_scraper"],
    # keywords='NLP,tokenizing,Chinese word segementation',
    # package_dir={'jieba':'jieba'},
    # package_data={'jieba':['*.*','finalseg/*','analyse/*','posseg/*']},

    # 依赖包
    install_requires=[
        'requests',
        "tenacity",
    ],
    classifiers=[
        # 'Development Status :: 4 - Beta',
        # 'Operating System :: Microsoft'  # 你的操作系统  OS Independent      Microsoft
        'Intended Audience :: Developers',
        # 'License :: OSI Approved :: MIT License',
        # 'License :: OSI Approved :: BSD License',  # BSD认证
        'Programming Language :: Python',  # 支持的语言
        'Programming Language :: Python :: 3',  # python版本 。。。
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Topic :: Software Development :: Libraries'
    ],
    zip_safe=True,
)