from manim import *

class FACA_ETL_Flow(Scene):
    def construct(self):
        # 將背景設定為純洋紅色，方便 CapCut 完美去背
        self.camera.background_color = "#FF00FF" 

        # --- 建立帶有陰影的立體區塊函數 ---
        def create_3d_node(text_str, width, height, border_color, fill_color):
            # 1. 陰影層 (向右下偏移 0.1)
            shadow = RoundedRectangle(corner_radius=0.2, width=width, height=height, 
                                      color=BLACK, fill_color=BLACK, fill_opacity=0.4)
            shadow.shift(RIGHT * 0.1 + DOWN * 0.1)
            
            # 2. 主體層
            box = RoundedRectangle(corner_radius=0.2, width=width, height=height, 
                                   color=border_color, fill_color=fill_color, fill_opacity=1.0)
            
            # 3. 文字層
            txt = Text(text_str, font_size=16).move_to(box)
            
            # 將陰影、主體、文字打包為一個群組
            return VGroup(shadow, box, txt)

        # --- Extract 區塊 ---
        ext_group = create_3d_node("Extract\n\n- AE/PE ISSUE LOG\n- Read: pdf, ppt, txt, xlsx", 
                                   3.5, 2.0, "#00FFFF", "#102027").shift(LEFT * 4.5)

        # --- Transform 區塊 ---
        langs = ["中文", "English", "Vietnamese", "Español", "Portuguese"]
        lang_nodes = VGroup()
        for lang in langs:
            node = create_3d_node(lang, 2.5, 0.6, PURPLE, "#2A1B38")
            lang_nodes.add(node)
        
        lang_nodes.arrange(DOWN, buff=0.2).move_to(ORIGIN)

        # --- Load 區塊 ---
        load_group = create_3d_node("Load\n\n- PE/AE Customized Formatting\n- Embedding -> Vector DBs", 
                                    3.5, 2.0, GREEN, "#1B3320").shift(RIGHT * 4.5)

        # --- 動態箭頭 (注意：箭頭需對齊 box，即索引 [1] 的位置) ---
        arrows_in = VGroup(*[Arrow(ext_group[1].get_right(), node[1].get_left(), buff=0.1, color="#00FFFF", max_tip_length_to_length_ratio=0.1) for node in lang_nodes])
        arrows_out = VGroup(*[Arrow(node[1].get_right(), load_group[1].get_left(), buff=0.1, color=GREEN, max_tip_length_to_length_ratio=0.1) for node in lang_nodes])


        # ================= 動畫展演 =================

        # 1. Extract 出場與閃爍
        self.play(FadeIn(ext_group, shift=RIGHT))
        self.play(Indicate(ext_group, scale_factor=1.05, color=WHITE), run_time=0.6)
        self.wait(0.2)

        # 2. 連接 Transform 的箭頭
        self.play(LaggedStart(*[GrowArrow(a) for a in arrows_in], lag_ratio=0.15))
        
        # 3. Transform 出場與逐一閃爍
        self.play(FadeIn(lang_nodes, shift=RIGHT))
        self.play(LaggedStart(*[Indicate(node, scale_factor=1.05, color=WHITE) for node in lang_nodes], lag_ratio=0.1))
        self.wait(0.2)

        # 4. 連接 Load 的箭頭
        self.play(LaggedStart(*[GrowArrow(a) for a in arrows_out], lag_ratio=0.15))
        
        # 5. Load 出場與閃爍
        self.play(FadeIn(load_group, shift=RIGHT))
        self.play(Indicate(load_group, scale_factor=1.05, color=WHITE), run_time=0.6)
        self.wait(1)

        # 6. 結束前的邊框跑馬燈亮起效果 (變更主體 box [1] 的邊框顏色)
        self.play(*[node[1].animate.set_stroke(color=YELLOW) for node in lang_nodes], run_time=0.5)
        self.play(*[node[1].animate.set_stroke(color=PURPLE) for node in lang_nodes], run_time=0.5)
        
        self.wait(3)