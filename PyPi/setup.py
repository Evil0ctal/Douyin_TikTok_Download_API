#! /usr/bin/env python
# -*- coding: utf-8 -*-
# RUN Command Line:
# 1.Build-check dist folder
# python setup.py sdist bdist_wheel
# 2.Upload to PyPi
# twine upload dist/*

import setuptools

with open("README.md", "r", encoding='utf8') as fh:
    long_description = fh.read()

setuptools.setup(
    name='douyin_tiktok_scraper',
    author='Evil0ctal',
    version='1.0.7',
    license='MIT License',
    description='Douyin/TikTok async data scraper.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author_email='Evil0ctal1985@gmail.com',
    url='https://github.com/Evil0ctal/Douyin_TikTok_Download_API',
    packages=setuptools.find_packages(),
    keywords='TikTok, Douyin, 抖音, Scraper, Crawler, API, Download, Video, No Watermark, Async',
    # 依赖包
    install_requires=[
        'aiohttp',
        "orjson",
        "tenacity",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3 :: Only",
    ],
    python_requires='>=3.6',
)
